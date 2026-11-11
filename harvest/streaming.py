"""Scan and add immediately; keep submitted operations pending across pauses."""
from collections import deque
from dataclasses import replace

from inventory import sort_button
from panels import PanelToggleNotFound
from warehouse_pages import StorageResult
from rules import batches
from windows_io import UserStopped, GameClosed, FocusLost, FocusRecoveryFailed


class StreamRunner:
    def __init__(self, engine, on_pause=None):
        self.e, self.on_pause = engine, on_pause
        self.mode, self.phase = 'corrode', 'open_panels'
        self.batch, self.skipped = [], set()
        self.plan = deque()
        self.synth_items = []
        self.corroded = self.synthesized = self.stored = 0
        self.reset_after_pause = False
        self.resume_activate = self.retry_deposit = False
        self.pending_slots = set()

    def run(self):
        while self.phase != 'done':
            try:
                if self.resume_activate:
                    self.e.game.expected_cursor = None
                    self.e.game.activate()
                    self.resume_activate = False
                if self.e.__dict__.get('_window_restore') is not None:
                    from window_restore import restore_positions
                    restore_positions(self.e)
                if self.retry_deposit:
                    self.retry_deposit = False
                    self.e.neutral()
                    unused, occupied = self.e.settled_board('synthesize', list(range(9)))
                    if occupied == [4]:
                        self.phase = 'deposit'
                if self.reset_after_pause:
                    # Filling has not submitted anything. Return partial inputs
                    # before starting a fresh scan; submitted phases are retained.
                    self.e.return_to_inventory(allowed=set(range(len(self.batch))) | self.pending_slots)
                    self.batch = []
                    self.pending_slots.clear()
                    self.reset_after_pause = False
                    self.plan.clear()
                    self.phase = 'sort' if self.mode == 'synthesize' else 'prepare'
                self.step()
            except (UserStopped, GameClosed):
                raise
            except Exception as error:
                if isinstance(error, FocusLost) and not isinstance(error, FocusRecoveryFailed):
                    self.e.note(f'任务暂时失焦（{self.phase}）：{error}')
                    try:
                        self.e.game.recover_focus()
                    except (UserStopped, GameClosed):
                        raise
                    except Exception as recovery_error:
                        # Preserve whether a commit was actually sent even
                        # when reacquisition itself needs manual assistance.
                        self.prepare_resume(error, activate=True)
                        error = recovery_error
                    else:
                        self.prepare_resume(error, activate=False)
                        continue
                if self.e.stop.is_set() or self.on_pause is None:
                    raise error
                self.e.note(f'任务暂停（{self.phase}）：{error}')
                self.on_pause(str(error))
                if self.e.stop.is_set():
                    raise UserStopped('已停止（F10 / 停止按钮）。')
                self.prepare_resume(error, activate=True)
        self.e.note(f'本次结束：入库 {self.stored} 格，腐蚀 {self.corroded} 批，合成 {self.synthesized} 批，'
                    f'跳过 {len(self.skipped)} 种物品。')

    def prepare_resume(self, error, *, activate):
        # A focus failure explicitly confirmed before mouse-down did not
        # submit the pre-recorded commit. All other result phases stay put.
        if (isinstance(error, FocusLost) and error.input_action == 'left_click'
                and error.input_started is False):
            if self.phase in ('corrode_result', 'synthesize_result'):
                self.phase = self.phase.replace('_result', '_click')
        self.resume_activate = activate
        if self.phase in ('fill', 'fill_synthesis'):
            self.reset_after_pause = True
        elif self.phase == 'deposit_result':
            self.retry_deposit = True

    def skip(self, item, reason):
        self.skipped.add(item.identity())
        self.e.note(f'背包 {item.slot + 1}：{reason}，本次跳过该物品。')

    def refresh_background(self):
        self.e.neutral()
        self.e.baseline_frame = self.e.game.frame()

    def restore_inputs(self, before, actual):
        if set(before) - set(actual):
            path = self.e.save_state('cube-input-missing')
            raise RuntimeError('本批已投入的魔方槽位缺失，请恢复物品或右键取回后继续。截图：' + path)
        extra = set(actual) - set(before)
        self.pending_slots.update(extra)
        if extra:
            self.e.return_to_inventory(slots=extra, allowed=set(before) | self.pending_slots)
            if self.e.occupied(self.mode) != before:
                path = self.e.save_state('cube-restore-unconfirmed')
                raise RuntimeError('右键取回后本批槽位尚未恢复，请处理后继续。截图：' + path)
        self.pending_slots.clear()

    def add(self, item):
        e = self.e
        e.require_action(self.mode, item)
        single_unconfirmed = self.mode == 'corrode' and item.kind == 'material' and item.count is None
        quantity = item.count if self.mode == 'corrode' and item.kind == 'material' else 1
        if single_unconfirmed:
            quantity = 1
            e.note(f'背包 {item.slot + 1}：腐蚀目标已确认、数量未确认，仅添加一次。')
        if type(quantity) is not int or not 1 <= quantity <= 5:
            self.skip(item, '物品数量未确认')
            return
        start = len(self.batch)
        group_before = list(range(start))
        placed, remaining, source_mode = 0, quantity, None

        def discard(actual, reason):
            # Roll back this source's entire series, preserving earlier items.
            # Keep both batch and pending slots until every return is proven.
            self.restore_inputs(group_before, actual)
            del self.batch[start:]
            self.skip(item, reason)
            self.refresh_background()

        while placed < quantity and len(self.batch) < 9:
            before = list(range(len(self.batch)))
            # Dismiss hover highlights before checking actual occupancy.
            e.neutral()
            actual = e.occupied(self.mode)
            if actual != before:
                unused, actual = e.settled_board(self.mode, list(range(9)))
            if actual != before:
                discard(actual, '发现本批之外的残留槽位，已右键取回')
                return
            self.pending_slots = {len(self.batch)}
            e.game.right_click(e.inventory_point(item.slot), alt=True)
            e.neutral()
            unused, after = e.settled_board(self.mode, list(range(9)))
            if after != list(range(len(self.batch) + 1)):
                discard(after, 'Alt＋右键添加结果不符合预期')
                return
            count, location, baseline = 1, 'cube', None
            if single_unconfirmed:
                # The user explicitly requests one selection without waiting
                # for a legible stack numeral. Never send a second shortcut.
                location = 'single_unconfirmed'
            elif item.kind == 'material':
                source = e.read_source_count(item, verify_identity=False)
                if type(source) is not int or not 0 <= source <= remaining:
                    discard(after, '添加后的材料数量未确认')
                    return
                if source == remaining:
                    # Each click selects one unit; the shared source is only
                    # debited when corrosion executes, not when it is selected.
                    location, baseline = 'inventory_units', quantity
                else:
                    count = e.read_stack_count(e.game.frame(), 'cube', e.board_points()[len(self.batch)])
                    if (type(count) is not int or not 1 <= count <= quantity - placed
                            or remaining - source != count):
                        discard(after, '添加后的材料数量未确认')
                        return
                if source_mode is not None and source_mode != location:
                    discard(after, '同一叠材料添加方式发生变化')
                    return
                source_mode, remaining = location, source
            self.batch.append(replace(item, count=count, count_location=location, source_count=baseline))
            self.pending_slots.clear()
            placed += count
            if location == 'cube' and (item.kind != 'material' or remaining == 0):
                e.vacated_slots = getattr(e, 'vacated_slots', set()) | {item.slot}
            e.note(f'边扫边投入：背包 {item.slot + 1} → 魔方 {len(self.batch)}，'
                   f'本格数量 {count}，当前物品已添加 {placed}/{quantity} 个。')
        if placed < quantity:
            e.note(f'魔方已满，背包 {item.slot + 1} 还需添加 {quantity - placed} 个；腐蚀整理后重新识别。')
        self.refresh_background()

    def accept(self, item):
        e = self.e
        if item.identity() in self.skipped:
            return False
        if e.action_allowed('synthesize', item):
            self.synth_items.append(item)
        if e.action_allowed('store', item):
            result = e.store_item(item, recognized=True)
            if result is StorageResult.FULL:
                self.skip(item, '所有仓库页复查后均无法接收，保留在背包')
            elif result is False:
                self.skip(item, 'Ctrl＋右键入库结果异常，已取回误入魔方的物品')
            else:
                self.stored += 1
            self.refresh_background()
        elif self.mode == 'corrode' and e.action_allowed(self.mode, item):
            self.add(item)
        return len(self.batch) == 9

    def choose_synthesis(self):
        if self.synthesized >= self.e.config['max_batches']:
            self.e.note('已达到合成本次批次数上限。')
            self.phase = 'collapse_panels'
            return
        plans = [batch for action, batch in batches(self.synth_items) if action == 'synthesize']
        if not plans:
            self.e.note('背包没有凑齐同品质九件的传奇或不朽装备，结束合成。')
            self.phase = 'collapse_panels'
            return
        self.plan = deque(plans[0])
        self.mode, self.phase = 'synthesize', 'prepare'
        self.e.note(f'已找到九件{self.plan[0].quality}装备，切换合成并按本次识别结果投入。')

    def step(self):
        e = self.e
        if self.phase == 'open_panels':
            if e.game.press_space_once():
                e.note('进入当前游戏进程，已按一次空格。')
            e.ensure_panels()
            self.phase = 'prepare_start'
        elif self.phase in ('prepare', 'prepare_start'):
            e.mode(self.mode)
            sort_button(e.game.frame(), e.config)
            if self.phase == 'prepare_start':
                # A previous interrupted warehouse transfer may have left holes.
                e.sort_inventory(self.mode)
            self.batch = []
            self.synth_items = []
            self.phase = 'fill_synthesis' if self.mode == 'synthesize' else 'fill'
        elif self.phase == 'fill':
            previous_stored = self.stored
            e.scan(on_item=self.accept)
            if self.batch and self.mode == 'corrode':
                self.counts = [i.count for i in self.batch]
                self.phase = 'corrode_click'
            elif self.stored > previous_stored:
                self.phase = 'sort'
            else:
                self.choose_synthesis()
        elif self.phase == 'scan_synthesis':
            if self.synthesized >= e.config['max_batches']:
                self.choose_synthesis()
                return
            self.synth_items = []
            previous_stored = self.stored
            e.scan(on_item=self.accept)
            if self.stored > previous_stored:
                self.phase = 'sort'
            else:
                self.choose_synthesis()
        elif self.phase == 'fill_synthesis':
            # Positions come from the completed scan; do not hover/OCR again.
            while self.plan:
                item = self.plan[0]
                e.show_inventory_item(item)
                self.add(item)
                self.plan.popleft()
            if len(self.batch) == 9:
                self.phase = 'synthesize_click'
            else:
                if self.batch:
                    e.return_to_inventory(allowed=set(range(len(self.batch))))
                    self.batch = []
                self.phase = 'sort_partial'
        elif self.phase in ('corrode_click', 'synthesize_click'):
            positions = ([n for n, count in enumerate(self.counts) if count]
                         if self.mode == 'corrode' else list(range(9)))
            if e.occupied(self.mode) != positions:
                raise RuntimeError('执行前魔方槽位发生变化，请恢复本批物品后继续。')
            if self.mode == 'synthesize':
                e.require_synthesis_batch(self.batch)
            e.check_options(self.mode)
            e.game.wait(max(0.5, float(e.config.get('action_interval_seconds', 0.8))))
            # Record the submitted phase BEFORE input; a pause cannot replay it.
            self.phase = 'corrode_result' if self.mode == 'corrode' else 'synthesize_result'
            e.game.click(e.config['points']['commit_' + self.mode])
            e.neutral()
        elif self.phase == 'corrode_result':
            self.counts, consumed = e.wait_corrode_progress(self.batch, self.counts, fast=True)
            e.note(f'本次已消耗 {sum(consumed)} 个。')
            if any(self.counts):
                self.phase = 'corrode_click'
            else:
                self.corroded += 1
                self.phase = 'sort'
        elif self.phase == 'synthesize_result':
            e.wait_synthesis_output()
            self.phase = 'deposit'
        elif self.phase == 'deposit':
            e.neutral()
            unused, occupied = e.settled_board('synthesize', list(range(9)))
            if occupied != [4]:
                raise RuntimeError('合成产物尚未在九宫格中间确认，未发送取回操作。')
            self.phase = 'deposit_result'
            e.game.right_click(e.board_points()[4])
            e.neutral()
        elif self.phase == 'deposit_result':
            e.confirm_synthesis_return()
            self.synthesized += 1
            self.phase = 'sort'
        elif self.phase in ('sort', 'sort_partial'):
            e.sort_inventory(self.mode)
            self.batch = []
            self.phase = 'scan_synthesis' if self.mode == 'synthesize' else 'prepare'
        elif self.phase == 'collapse_panels':
            try:
                e.collapse_panels()
            except PanelToggleNotFound as error:
                if error.action != 'collapse':
                    raise
                e.note('本进程收菜已完成，但收起按钮无法确认，保留当前页面并继续队列。' + str(error))
            self.phase = 'done'
