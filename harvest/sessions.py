"""Process a fixed list of game instances one at a time."""
import copy
from dataclasses import dataclass, field

from engine import Engine
from windows_io import GameClosed, UserStopped, check_stop, find_games
from window_isolation import GameIsolation


@dataclass
class HarvestResult:
    total: int
    completed: int = 0
    skipped: int = 0
    folders: list[str] = field(default_factory=list)

    def summary(self):
        return f'本次多开收菜结束：共 {self.total} 个进程，完成 {self.completed} 个，已关闭跳过 {self.skipped} 个。'


def harvest_all(config, stop, log, progress, on_pause=None, *, on_record=None):
    check_stop(stop)
    targets = find_games(log=log)
    if not targets:
        raise RuntimeError('未找到可操作的 TaskbarHero 游戏窗口，请先打开当前目录中的游戏。')
    with GameIsolation(targets, stop, log) as windows:
        return harvest_targets(config, stop, log, progress, on_pause, targets, windows, on_record)


def harvest_targets(config, stop, log, progress, on_pause, targets, windows, on_record):
    result = HarvestResult(len(targets))
    panel_toggle_hint = None
    log(f'检测到 {len(targets)} 个 TaskbarHero 游戏进程，按 PID 从小到大依次收菜。')
    for index, target in enumerate(targets, 1):
        check_stop(stop)
        prefix = f'[{index}/{len(targets)} · PID {target.pid}] '
        progress([])
        log(prefix + '开始处理。')

        def instance_log(text, prefix=prefix):
            log(prefix + text)

        def instance_pause(message, prefix=prefix):
            on_pause(prefix + message)

        engine = None
        while engine is None:
            check_stop(stop)
            try:
                windows.select(target)
                engine = Engine(copy.deepcopy(config), stop, instance_log, progress, target=target)
            except GameClosed as error:
                result.skipped += 1
                log(prefix + '已关闭，跳过该进程。' + str(error))
                break
            except UserStopped:
                raise
            except Exception as error:
                check_stop(stop)
                if on_pause is None:
                    raise
                log(prefix + '启动暂停：' + str(error))
                instance_pause(str(error))
                check_stop(stop)
        if engine is None:
            continue
        try:
            engine.game._visual_guard = lambda target=target: windows.ensure(target)
            if panel_toggle_hint is not None:
                engine._panel_toggle_hint = list(panel_toggle_hint)
            if on_record is not None:
                on_record(str(engine.folder))
            engine.execute_stream(instance_pause if on_pause is not None else None)
            check_stop(stop)
            confirmed = engine.__dict__.get('_panel_toggle_confirmed')
            if isinstance(confirmed, (list, tuple)) and len(confirmed) == 2:
                panel_toggle_hint = list(confirmed)
            result.completed += 1
            result.folders.append(str(engine.folder))
            log(prefix + '收菜完成。记录：' + str(engine.folder))
        except GameClosed as error:
            result.skipped += 1
            log(prefix + '处理期间窗口已关闭，跳过该进程。' + str(error))
        finally:
            try:
                engine.close()
            except UserStopped:
                raise
            except Exception as error:
                log(prefix + '关闭识别服务时出错：' + str(error))
    log(result.summary())
    return result
