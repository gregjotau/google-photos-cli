"""Re-upload optimized streams in Original quality after Storage saver rewrites.

The original source and the first encoded output remain local. A container-only
remux changes the upload hash without another lossy encode. The previous cloud
replacement is removed only after the new upload passes the normal video gates.
"""

import json
from pathlib import Path

from migration import save
from photos_api import at, upload
from video_codec import remux_original_quality
from video_migration import Videos, WORK, sha


class VideoRepair(Videos):
    def write(self, state, **kw):
        parent = state.get("_repair_parent")
        if parent:
            state.update(kw)
            save(WORK / parent / "quality-repair.json", state)
        else:
            super().write(state, **kw)

    def process(self, row):
        parent = row[0]
        path = WORK / parent / "state.json"
        if not path.exists():
            return
        source = json.loads(path.read_text())
        if source.get("account") != self.account:
            raise RuntimeError("Video repair journal account mismatch")
        old = source.get("replacement_key")
        if not old:
            return
        ext = self.api.extended(old)
        if at(ext, 0, 30, 2) == 2 and at(ext, 0, 5) == source.get("encoded_bytes"):
            if source.get("stage") != "trashed":
                with self.lock:
                    self.waiting.append(source)
            return
        repair_path = WORK / parent / "quality-repair.json"
        if repair_path.exists():
            repair = json.loads(repair_path.read_text())
            if repair.get("original_key") != old:
                raise RuntimeError(
                    "Repair source changed; retain all candidates for reconciliation"
                )
        else:
            if sha(source["source"]) != source["original_sha256"]:
                raise RuntimeError("Original backup changed; refusing repair")
            if sha(source["output"]) != source["encoded_sha256"]:
                raise RuntimeError("Local encoded backup changed; refusing repair")
            output = Path(source["output"])
            target = output.with_name(output.stem + "-original-quality.mp4")
            remux_original_quality(output, target)
            repair = {
                "_repair_parent": parent,
                "account": self.account,
                "original_key": old,
                "original_info": self.api.info(old),
                "original_ext": source["original_ext"],
                "original_bytes": at(ext, 0, 5),
                "inventory_row": source["inventory_row"],
                "filename": target.name,
                "source": source["source"],
                "original_sha256": source["original_sha256"],
                "output": str(target),
                "encoded_bytes": target.stat().st_size,
                "encoded_sha256": sha(target),
                "conversion": source.get("conversion", {}),
                "stage": "prepared",
            }
            self.write(repair)
        if not repair.get("replacement_key"):
            key = upload(
                self.api,
                repair["output"],
                source["original_ext"][0][3],
                lambda **kw: self.write(repair, **kw),
            )
            if key == old:
                raise RuntimeError("Repair upload did not create a distinct item")
            self.write(repair, stage="uploaded", replacement_key=key)
            self.log("original_quality_uploaded", key=parent, replacement=key)
        with self.lock:
            self.waiting.append(repair)

    def finalize(self, state):
        if not super().finalize(state):
            return False
        parent = state.get("_repair_parent")
        if not parent:
            return True
        source = json.loads((WORK / parent / "state.json").read_text())
        old = source["replacement_key"]
        new = state["replacement_key"]
        if old != new:
            if old != state["original_key"]:
                raise RuntimeError("Authoritative video journal changed during repair")
            history = source.get("superseded_replacements", [])
            history.append(
                {
                    "key": old,
                    "encoded_sha256": source["encoded_sha256"],
                    "reason": "Google Storage saver rewrote canonical media; replaced after verification",
                }
            )
            was_done = source.get("stage") == "trashed"
            self.write(
                source,
                replacement_key=new,
                encoded_bytes=state["encoded_bytes"],
                encoded_sha256=state["encoded_sha256"],
                output=state["output"],
                download_verified_sha256=state["download_verified_sha256"],
                playback_ready=True,
                capture_date_verified=True,
                albums_preserved=True,
                upload_quality="original",
                superseded_replacements=history,
                stage="trashed" if was_done else "uploaded",
                error=None,
            )
            if not was_done:
                return super().finalize(source)
        self.log("original_quality_repaired", key=parent)
        return True
