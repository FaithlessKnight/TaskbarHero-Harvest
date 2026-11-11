"""Compare full game paths, including Sandboxie's default drive mapping."""
import ntpath
import re


def path_key(path):
    if not isinstance(path, str) or not path or '\x00' in path:
        return None
    path = path.replace('/', '\\')
    if path.startswith('\\\\?\\'):
        path = path[4:]
    if not re.match(r'^[A-Za-z]:\\', path):
        return None
    # Process image paths should already be canonical; do not let traversal
    # change which part of a path is interpreted as the sandbox's drive root.
    if any(part in ('.', '..') for part in path.split('\\')):
        return None
    return ntpath.normcase(ntpath.normpath(path))


def sandboxie_original_path(path):
    key = path_key(path)
    if key is None:
        return None
    # Default Sandboxie layout observed in the supplied diagnostic report:
    # C:\Sandbox\<user>\<box>\drive\D\<original path relative to D:\>.
    match = re.fullmatch(r'[a-z]:\\sandbox\\[^\\:]+\\[^\\:]+\\drive\\([a-z])\\([^:]+)', key)
    return match[1] + ':\\' + match[2] if match else None


def same_game_installation(actual, expected):
    actual, expected = path_key(actual), path_key(expected)
    if actual is None or expected is None:
        return False
    return (sandboxie_original_path(actual) or actual) == (sandboxie_original_path(expected) or expected)
