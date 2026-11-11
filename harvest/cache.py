"""Clear only generated assistant files, without following directory links."""
from dataclasses import dataclass, field
from pathlib import Path
import stat


@dataclass
class CacheResult:
    files: int = 0
    bytes_freed: int = 0
    failures: list[str] = field(default_factory=list)

    def summary(self):
        size = self.bytes_freed / (1024 * 1024)
        text = f'已清理 {self.files} 个缓存文件，释放 {size:.2f} MB。校准和设置已保留。'
        if self.failures:
            text += f'另有 {len(self.failures)} 处未清理，详情见下方日志。'
        return text


def clear_cache(root, *, keep_runs=(), runs_only=False):
    root = Path(root).resolve(strict=True)
    result = CacheResult()
    protected = {root / 'runs' / name for name in keep_runs}

    def linked(info):
        return (stat.S_ISLNK(info.st_mode) or
                getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    def remove(path, *, keep_directory=False):
        if path in protected:
            return True
        try:
            info = path.lstat()
        except FileNotFoundError:
            return True
        except OSError as error:
            result.failures.append(f'{path.relative_to(root)}：{error}')
            return False
        try:
            # Reject Windows junctions as well as symbolic links before descent.
            if linked(info):
                raise ValueError('跳过链接，未访问链接目标')
            resolved = path.resolve(strict=True)
            if resolved == root or not resolved.is_relative_to(root):
                raise ValueError('缓存路径超出助手目录，未清理')
            if stat.S_ISDIR(info.st_mode):
                children = list(path.iterdir())
                complete = True
                for child in children:
                    if not remove(child):
                        complete = False
                if complete and not keep_directory:
                    path.rmdir()
                return complete
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('跳过非普通文件')
            path.unlink()
            result.files += 1
            result.bytes_freed += info.st_size
            return True
        except FileNotFoundError:
            return True
        except (OSError, ValueError) as error:
            result.failures.append(f'{path.relative_to(root)}：{error}')
            return False

    # Never scan the whole installation: configuration, assets and fixtures stay.
    targets = ('runs',) if runs_only else ('runs', '__pycache__', 'tests/__pycache__', 'last-error.txt')
    for relative in targets:
        target = root / relative
        try:
            parent = target.parent
            while parent != root:
                if linked(parent.lstat()):
                    raise ValueError('缓存的上级目录是链接，未访问链接目标')
                parent = parent.parent
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            result.failures.append(f'{relative}：{error}')
            continue
        remove(target, keep_directory=relative == 'runs')
    return result


def retain_latest_runs(root, folders):
    """Keep every process record of this round; do nothing if none was created."""
    root = Path(root).resolve(strict=True)
    runs = root / 'runs'
    names = set()
    for folder in folders:
        path = Path(folder).absolute()
        # Validate every supplied path before deleting any old record.
        if path.parent != runs or path.resolve() != path or not path.is_dir():
            raise ValueError('本轮记录路径不在 runs 直属目录内，未清理旧记录。')
        names.add(path.name)
    if not names:
        return CacheResult()
    return clear_cache(root, keep_runs=names, runs_only=True)
