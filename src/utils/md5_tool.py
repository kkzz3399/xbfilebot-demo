import os
import json
import traceback
import hashlib
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# 尝试使用 libmagic 做 mime 检测（更可靠），若不可用则回退到扩展名判断
try:
    import magic  # pip install python-magic
    _HAS_MAGIC = True
except Exception:
    _HAS_MAGIC = False

# 默认允许修改 MD5 的扩展（小写，不含点）
DEFAULT_EDITABLE_EXT = {
    "jpg", "jpeg", "png", "gif", "webp",    # 图片
    "mp4", "mkv", "avi", "mov",             # 视频
    "zip", "rar", "7z"                      # 压缩（注意：zip 可能会被破坏，使用前确认）
}

# 修改记录文件（可替换为 DB）
MODIFY_LOG_FILE = Path(".md5_modify_log.json")


def _is_editable_by_mime(path: Path, allowed_exts: Optional[set] = None) -> bool:
    ext = path.suffix.lower().lstrip(".")
    if allowed_exts and ext not in allowed_exts:
        return False
    if _HAS_MAGIC:
        try:
            m = magic.from_file(str(path), mime=True)
            # 图片/视频/zip 的常见 mime 前缀判断
            if m is None:
                return False
            if m.startswith("image/") or m.startswith("video/"):
                return True
            if m in ("application/zip", "application/x-rar-compressed", "application/x-7z-compressed"):
                return True
            return False
        except Exception:
            # 若 magic 检测失败，回退到扩展名判断
            return ext in (allowed_exts or DEFAULT_EDITABLE_EXT)
    else:
        # 无 libmagic，使用扩展名判断（fallback）
        return ext in (allowed_exts or DEFAULT_EDITABLE_EXT)


def _load_log() -> Dict[str, Dict]:
    if MODIFY_LOG_FILE.exists():
        try:
            return json.loads(MODIFY_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_log(log: Dict[str, Dict]):
    MODIFY_LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def compute_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def modify_md5_all(
    folder_paths: List[str],
    include_subdirs: bool = False,
    append_bytes: int = 1,
    dry_run: bool = False,
    allowed_exts: Optional[set] = None,
    backup_before_modify: bool = True,
) -> Tuple[int, List[Dict]]:
    """
    对 folder_paths 中符合类型的文件 append 随机字节以改变 MD5。
    返回 (modified_count, details_list)
    details_list 中每项 dict 包含:
      - path, original_size, new_size, original_md5, new_md5, status, error (if any)

    参数说明：
    - folder_paths: 根目录列表（绝对或相对路径）
    - include_subdirs: 是否递归子目录
    - append_bytes: 追加字节数（默认 1）
    - dry_run: 若为 True，不实际写文件，仅返回将被处理的文件列表
    - allowed_exts: 可编辑扩展名集合（小写，不含点），若 None 使用默认 DEFAULT_EDITABLE_EXT
    - backup_before_modify: 若 True，会在文件同目录生成 .bak_{timestamp} 备份文件（对大批量文件慎用）
    """
    allowed = allowed_exts or DEFAULT_EDITABLE_EXT
    details = []
    modified_count = 0
    log = _load_log()

    for root in folder_paths:
        p_root = Path(root)
        if not p_root.exists():
            details.append({"path": root, "status": "not_found", "error": "root_not_exists"})
            continue

        if include_subdirs:
            iterator = [f for f in p_root.rglob("*") if f.is_file()]
        else:
            iterator = [f for f in p_root.iterdir() if f.is_file()]

        for f in iterator:
            try:
                if not _is_editable_by_mime(f, allowed):
                    details.append({"path": str(f), "status": "skipped_type"})
                    continue

                orig_size = f.stat().st_size
                orig_md5 = compute_md5(f)

                if dry_run:
                    details.append({
                        "path": str(f),
                        "status": "dry_run",
                        "original_size": orig_size,
                        "original_md5": orig_md5,
                    })
                    continue

                # 备份（可选）——备份文件名： file.bak_{timestamp}
                if backup_before_modify:
                    bak_path = f.with_name(f.name + ".bak")
                    # 如果 bak 存在则不覆盖
                    if not bak_path.exists():
                        try:
                            shutil.copy2(str(f), str(bak_path))
                        except Exception as bex:
                            # 备份失败但我们可以选择继续或中断，记录并继续
                            details.append({"path": str(f), "status": "backup_failed", "error": str(bex)})
                            # 继续不强制中断

                # 执行追加写入
                with f.open("ab") as fh:
                    fh.write(os.urandom(append_bytes))

                new_size = f.stat().st_size
                new_md5 = compute_md5(f)

                # 记录到日志（便于回滚）
                log_entry = {
                    "original_size": orig_size,
                    "new_size": new_size,
                    "original_md5": orig_md5,
                    "new_md5": new_md5,
                }
                log[str(f)] = log_entry
                _save_log(log)

                modified_count += 1
                details.append({
                    "path": str(f),
                    "status": "modified",
                    "original_size": orig_size,
                    "new_size": new_size,
                    "original_md5": orig_md5,
                    "new_md5": new_md5,
                })
            except Exception as e:
                details.append({"path": str(f), "status": "error", "error": repr(e), "trace": traceback.format_exc()})

    return modified_count, details


def rollback_modifications(paths: Optional[List[str]] = None) -> List[Dict]:
    """
    回滚之前记录在 log 中的修改。若 paths 提供，则只回滚这些路径；否则回滚 log 中所有条目。
    回滚策略：truncate 到 original_size（仅在记录中存在 original_size 时有效）。
    返回回滚详情列表。
    """
    log = _load_log()
    results = []

    items = [(p, v) for p, v in log.items() if (paths is None or p in paths)]

    for p, meta in items:
        try:
            f = Path(p)
            if not f.exists():
                results.append({"path": p, "status": "missing"})
                continue
            orig_size = meta.get("original_size")
            if orig_size is None:
                results.append({"path": p, "status": "no_original_size"})
                continue
            # 截断文件为原始大小
            with f.open("r+b") as fh:
                fh.truncate(orig_size)
            # 更新日志：删除或标记已回滚
            log.pop(p, None)
            _save_log(log)
            results.append({"path": p, "status": "rolled_back", "original_size": orig_size})
        except Exception as e:
            results.append({"path": p, "status": "error", "error": repr(e)})
    return results