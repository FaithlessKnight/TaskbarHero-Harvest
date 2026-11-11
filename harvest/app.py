"""TaskbarHero inventory assistant. Start with Start.cmd; Python 3.10+ and Windows."""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import traceback

from auto_harvest import AutoHarvest, interval_minutes
from cache import clear_cache, retain_latest_runs
from engine import Engine, POINT_LABELS, REGION_LABELS, learn, validate
from rules import corrosion_selection, SELECTABLE_QUALITIES, CORROSION_TYPES
from sessions import harvest_all
from supplemental import normalize_rules
from windows_io import Game, ROOT, dpi_aware, check_stop, UserStopped, find_games


CONFIG = ROOT / 'config.json'
DEFAULTS = {'version': 1, 'points': {}, 'regions': {}, 'learned': {},
            'bag_columns': 7, 'bag_rows': 3, 'bag_total_rows': 15, 'warehouse_columns': 7, 'warehouse_rows': 7,
            'hover_seconds': 1.2, 'action_interval_seconds': 0.8, 'max_batches': 20,
            'auto_interval_minutes': 30, 'supplemental_items': []}
ACTION_NAMES = {'keep': '保留', 'corrode': '腐蚀', 'synthesize': '合成', 'store': '入库'}
KIND_NAMES = {'equipment': '装备', 'material': '材料', '': '未确认'}


class App:
    def __init__(self, root):
        self.root = root
        self.root.title('TaskbarHero 收菜助手 1.49')
        self.root.geometry('1260x830')
        self.root.minsize(980, 680)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.auto_harvest = AutoHarvest()
        self.closing = False
        self.display_items = []
        self.frame = None
        self.rect_start = None
        self.config = copy.deepcopy(DEFAULTS)
        if CONFIG.exists():
            self.config.update(json.loads(CONFIG.read_text(encoding='utf-8')))
        self.config['corrosion_selection'] = corrosion_selection(self.config.get('corrosion_selection'))
        self.config['supplemental_items'] = normalize_rules(self.config.get('supplemental_items'))
        self.status = tk.StringVar(value='完成窗口校准后，点击“开始收菜”即可边扫描边执行。')
        self.book = ttk.Notebook(root)
        self.book.pack(fill='both', expand=True, padx=10, pady=10)
        self.control = ttk.Frame(self.book, padding=12)
        self.calibration = ttk.Frame(self.book, padding=12)
        self.book.add(self.control, text='收菜与记录')
        self.book.add(self.calibration, text='窗口校准')
        self.build_control()
        self.build_calibration()
        from preferences_ui import PreferencesUI
        self.preferences = PreferencesUI(self)
        self.refresh_controls()
        footer = ttk.Frame(root)
        footer.pack(fill='x', padx=12, pady=(0, 8))
        ttk.Label(footer, textvariable=self.status).pack(side='left', fill='x', expand=True)
        ttk.Label(footer, text='作者：TheFallen').pack(side='right', padx=(12, 0))
        root.after(100, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def build_control(self):
        toolbar = ttk.Frame(self.control)
        toolbar.pack(fill='x')
        self.hover_button = ttk.Button(toolbar, text='测试第一格', command=self.start_hover_test)
        self.hover_button.pack(side='left', padx=(0, 8))
        self.run_button = ttk.Button(toolbar, text='开始收菜（边扫边执行）', command=self.start_execution)
        self.run_button.pack(side='left', padx=(0, 8))
        self.auto_button = ttk.Button(toolbar, text='自动收菜', command=self.start_auto)
        self.auto_button.pack(side='left', padx=(0, 8))
        self.return_button = ttk.Button(toolbar, text='取回魔方物品', command=self.start_return)
        self.return_button.pack(side='left', padx=(0, 8))
        ttk.Button(toolbar, text='停止（F10）', command=self.request_stop).pack(side='left')
        ttk.Button(toolbar, text='打开记录目录', command=self.open_runs).pack(side='right')
        self.cache_button = ttk.Button(toolbar, text='清理缓存', command=self.start_clear_cache)
        self.cache_button.pack(side='right', padx=(0, 8))
        ttk.Button(toolbar, text='多开诊断', command=self.start_diagnostics).pack(side='right', padx=(0, 8))
        settings = ttk.Frame(self.control)
        settings.pack(fill='x', pady=12)
        self.setting_vars = {}
        for column, (key, label, low, high) in enumerate((
                ('bag_columns', '背包列数', 2, 12), ('bag_rows', '背包可见行数', 2, 3),
                ('bag_total_rows', '背包总行数', 3, 15),
                ('warehouse_columns', '仓库列数', 2, 12), ('warehouse_rows', '仓库行数', 2, 15),
                ('max_batches', '合成最多批数', 1, 100))):
            ttk.Label(settings, text=label).grid(row=0, column=column * 2, padx=(0, 4))
            var = tk.IntVar(value=self.config[key])
            self.setting_vars[key] = var
            ttk.Spinbox(settings, from_=low, to=high, textvariable=var, width=4).grid(row=0, column=column * 2 + 1, padx=(0, 12))
        ttk.Label(settings, text='悬停等待（秒）').grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.hover_seconds = tk.DoubleVar(value=self.config['hover_seconds'])
        ttk.Spinbox(settings, from_=0.3, to=5, increment=0.1, textvariable=self.hover_seconds,
                    width=5).grid(row=1, column=2, sticky='w', pady=(6, 0))
        ttk.Label(settings, text='执行间隔（秒）').grid(row=1, column=3, columnspan=2, sticky='w', pady=(6, 0))
        self.action_interval = tk.DoubleVar(value=self.config['action_interval_seconds'])
        ttk.Spinbox(settings, from_=0.5, to=10, increment=0.1, textvariable=self.action_interval,
                    width=5).grid(row=1, column=5, sticky='w', pady=(6, 0))
        ttk.Label(settings, text='自动间隔（分钟）').grid(row=1, column=6, columnspan=2, sticky='w', pady=(6, 0))
        self.auto_minutes = tk.StringVar(value=str(self.config['auto_interval_minutes']))
        ttk.Spinbox(settings, from_=1, to=1440, increment=1, textvariable=self.auto_minutes,
                    width=6).grid(row=1, column=8, sticky='w', pady=(6, 0))
        self.auto_minutes.trace_add('write', self.auto_interval_changed)
        self.auto_status = tk.StringVar(value='自动收菜未开启')
        ttk.Label(settings, textvariable=self.auto_status).grid(row=2, column=0, columnspan=12, sticky='w', pady=(6, 0))
        choices = ttk.LabelFrame(self.control, text='腐蚀范围：勾选对应类型和品质', padding=(10, 4))
        choices.pack(fill='x', pady=(0, 10))
        self.corrosion_vars = {}
        qualities = [quality for quality in SELECTABLE_QUALITIES if quality != '至宝']
        for column, quality in enumerate(qualities, 1):
            ttk.Label(choices, text=quality).grid(row=0, column=column, padx=18)
        for row, (kind, label) in enumerate(CORROSION_TYPES.items(), 1):
            ttk.Label(choices, text=label).grid(row=row, column=0, sticky='w', padx=(0, 20))
            self.corrosion_vars[kind] = {}
            for column, quality in enumerate(qualities, 1):
                var = tk.BooleanVar(value=quality in self.config['corrosion_selection'][kind])
                self.corrosion_vars[kind][quality] = var
                ttk.Checkbutton(choices, variable=var, command=self.selection_changed).grid(row=row, column=column)
        ttk.Label(choices, text='瘟疫之果不限品质腐蚀；供奉材料及其他指定高品质入库。锁定物品保留。传奇、不朽装备未勾选腐蚀时各九件合成。\n'
                  '铭刻材料：普通、罕见、稀有腐蚀，其余品质入库，不受上方勾选影响。').grid(
            row=3, column=0, columnspan=len(qualities) + 1, sticky='w', pady=(4, 0))
        self.tree = ttk.Treeview(self.control, columns=('slot', 'action', 'quality', 'kind', 'level', 'count', 'reason'), show='headings', height=6)
        for key, label, width in (('slot', '格子', 55), ('action', '处理', 65), ('quality', '品质', 65),
                                  ('kind', '类型', 80), ('level', '等级', 65), ('count', '数量', 55), ('reason', '依据', 460)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=45)
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.show_item)
        supplement = ttk.Frame(self.control)
        supplement.pack(fill='x', pady=(6, 0))
        self.supplement_button = ttk.Button(supplement, text='补充选中物品…',
            command=lambda: self.preferences.from_record())
        self.supplement_button.pack(side='left')
        ttk.Label(supplement, text='选中识别清单中的物品，可补充其腐蚀或入库规则。').pack(side='left', padx=10)
        self.details = tk.Text(self.control, height=5, wrap='word', state='disabled')
        self.details.pack(fill='x', pady=(10, 0))
        self.log_text = tk.Text(self.control, height=4, wrap='word', state='disabled')
        self.log_text.pack(fill='x', pady=(10, 0))

    def build_calibration(self):
        left = ttk.Frame(self.calibration, width=315)
        left.pack(side='left', fill='y', padx=(0, 12))
        buttons = ttk.Frame(left)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='截取窗口（3 秒后）', command=self.take_frame).pack(fill='x')
        ttk.Button(buttons, text='保存校准', command=self.save_clicked).pack(fill='x', pady=6)
        self.roles = list(POINT_LABELS) + list(REGION_LABELS)
        self.role_list = tk.Listbox(left, height=19, exportselection=False)
        self.role_list.pack(fill='both', expand=True)
        self.role_list.bind('<<ListboxSelect>>', lambda event: setattr(self, 'rect_start', None))
        self.render_roles()
        self.role_list.selection_set(0)
        self.ready_empty = tk.BooleanVar(value=False)
        self.ready_backpack = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text='九宫格已清空', variable=self.ready_empty).pack(anchor='w', pady=(10, 0))
        ttk.Checkbutton(left, text='已取消“包含仓库物品”', variable=self.ready_backpack).pack(anchor='w')
        ttk.Button(left, text='记住当前腐蚀界面', command=lambda: self.start_learn('corrode')).pack(fill='x', pady=(8, 4))
        ttk.Button(left, text='记住当前合成界面', command=lambda: self.start_learn('synthesize')).pack(fill='x')
        self.learn_status = tk.StringVar()
        ttk.Label(left, textvariable=self.learn_status).pack(anchor='w', pady=8)
        self.render_learned()
        right = ttk.Frame(self.calibration)
        right.pack(side='left', fill='both', expand=True)
        self.canvas = tk.Canvas(right, background='#202020', highlightthickness=0)
        bar_x, bar_y = ttk.Scrollbar(right, orient='horizontal', command=self.canvas.xview), ttk.Scrollbar(right, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=bar_x.set, yscrollcommand=bar_y.set)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        bar_x.grid(row=1, column=0, sticky='ew')
        bar_y.grid(row=0, column=1, sticky='ns')
        self.canvas.bind('<Button-1>', self.pick)

    def render_roles(self):
        selection = self.role_list.curselection()
        self.role_list.delete(0, 'end')
        for key in self.roles:
            is_region = key in REGION_LABELS
            done = key in self.config['regions' if is_region else 'points']
            self.role_list.insert('end', ('[已设] ' if done else '[待设] ') +
                                  (REGION_LABELS if is_region else POINT_LABELS)[key] + ('（框选）' if is_region else ''))
        if selection:
            self.role_list.selection_set(selection[0])

    def render_learned(self):
        self.learn_status.set('腐蚀：' + ('已记录' if 'corrode' in self.config['learned'] else '待记录') +
                              '　合成：' + ('已记录' if 'synthesize' in self.config['learned'] else '待记录'))

    def selection_changed(self):
        self.refresh_controls()
        self.status.set('腐蚀范围已修改，开始收菜会按当前勾选边扫描边执行。')

    def settings(self):
        for key, var in self.setting_vars.items():
            value = var.get()
            if not (1 <= value <= 100):
                raise ValueError('行列数和批数设置无效。')
            self.config[key] = value
        delay = self.hover_seconds.get()
        if not (0.3 <= delay <= 5):
            raise ValueError('悬停等待时间需在 0.3 到 5 秒之间。')
        self.config['hover_seconds'] = delay
        interval = self.action_interval.get()
        if not (0.5 <= interval <= 10):
            raise ValueError('执行间隔需在 0.5 到 10 秒之间。')
        self.config['action_interval_seconds'] = interval
        if hasattr(self, 'auto_minutes'):
            try:
                minutes = interval_minutes(self.auto_minutes.get())
            except (ValueError, tk.TclError):
                if not self.auto_enabled():
                    raise
                minutes = self.auto_harvest.minutes
            self.config['auto_interval_minutes'] = minutes
        self.config['corrosion_selection'] = {
            kind: [quality for quality, var in qualities.items() if var.get()]
            for kind, qualities in self.corrosion_vars.items()}

    def save(self):
        self.settings()
        self.write_config()
        self.refresh_controls()

    def write_config(self, config=None):
        temporary = CONFIG.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.config if config is None else config, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(CONFIG)

    def reload_settings(self):
        self._loading_settings = True
        try:
            for key, var in self.setting_vars.items():
                var.set(self.config[key])
            self.hover_seconds.set(self.config['hover_seconds'])
            self.action_interval.set(self.config['action_interval_seconds'])
            self.auto_minutes.set(str(self.config['auto_interval_minutes']))
            self.auto_harvest.change_interval(self.config['auto_interval_minutes'])
            for kind, values in self.corrosion_vars.items():
                for quality, var in values.items():
                    var.set(quality in self.config['corrosion_selection'][kind])
        finally:
            self._loading_settings = False

    def auto_enabled(self):
        auto = getattr(self, 'auto_harvest', None)
        return auto is not None and auto.enabled

    def refresh_controls(self):
        locked = self.busy or self.auto_enabled()
        for name in ('cache_button', 'hover_button', 'run_button', 'return_button', 'supplement_button'):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(state='disabled' if locked else 'normal')
        if hasattr(self, 'auto_button'):
            self.auto_button.configure(text='停止自动收菜' if self.auto_enabled() else '自动收菜',
                                       state='normal' if self.auto_enabled() or not self.busy else 'disabled')
        if hasattr(self, 'preferences'):
            self.preferences.refresh()

    def auto_interval_changed(self, *unused):
        if getattr(self, '_loading_settings', False):
            return
        try:
            minutes = interval_minutes(self.auto_minutes.get())
        except (ValueError, tk.TclError):
            self.auto_status.set('请输入 1～1440 分钟；修改完成前沿用原间隔。')
            return
        self.config['auto_interval_minutes'] = minutes
        self.auto_harvest.change_interval(minutes)
        try:
            self.write_config()
        except OSError as error:
            self.status.set('自动间隔暂未保存：' + str(error))

    def start_auto(self):
        if self.auto_enabled():
            self.request_stop()
            return
        if self.busy:
            return
        try:
            minutes = interval_minutes(self.auto_minutes.get())
        except (ValueError, tk.TclError) as error:
            messagebox.showerror('自动收菜设置错误', str(error))
            return
        self.auto_harvest.start(minutes)
        self.stop.clear()
        if not self.start_execution(automatic=True):
            self.auto_harvest.cancel()
        self.refresh_controls()

    def request_stop(self):
        self.stop.set()
        if self.auto_enabled():
            self.auto_harvest.cancel()
            self.auto_status.set('自动收菜已停止')
        if not self.busy:
            self.status.set('已停止，后续不会自动收菜。')
        self.refresh_controls()

    def tick_auto(self):
        if not self.auto_enabled():
            return
        try:
            check_stop(self.stop)
        except UserStopped:
            self.request_stop()
            return
        if self.busy:
            self.auto_status.set('自动收菜执行中；整轮完成后开始计时')
            return
        seconds = self.auto_harvest.remaining()
        if seconds is None:
            return
        self.auto_status.set(f'自动收菜：距下一轮 {seconds // 60:02d}:{seconds % 60:02d}')
        if self.auto_harvest.take_due():
            if not self.start_execution(automatic=True):
                self.auto_harvest.cancel()
                self.auto_status.set('自动收菜已停止，请检查配置后重新开启。')
                self.refresh_controls()

    def save_clicked(self):
        if self.busy or self.auto_enabled():
            return
        try:
            self.save()
            self.status.set('校准已保存。')
        except Exception as error:
            messagebox.showerror('保存失败', str(error))

    def worker(self, task, on_done):
        if self.busy:
            return
        self.busy = True
        self.stop.clear()
        self.refresh_controls()

        def run():
            try:
                result = task()
                self.events.put(('done', (on_done, result)))
            except Exception as error:
                (ROOT / 'last-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
                self.events.put(('error', str(error)))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind in ('done', 'error'):
                    self.busy = False
                    self.root.deiconify()
                    self.refresh_controls()
                if kind == 'done':
                    callback, result = value
                    callback(result)
                elif kind == 'error':
                    if self.auto_enabled():
                        self.auto_harvest.cancel()
                        self.auto_status.set('自动收菜已停止，请处理错误后重新开启。')
                        self.refresh_controls()
                    self.status.set(value)
                    messagebox.showerror('已停止', value)
                elif kind == 'log':
                    self.status.set(value)
                    self.log_text.configure(state='normal')
                    self.log_text.insert('end', datetime.now().strftime('%H:%M:%S ') + value + '\n')
                    self.log_text.see('end')
                    self.log_text.configure(state='disabled')
                elif kind == 'items':
                    self.render_items(value)
                elif kind == 'round_start':
                    self.render_items([])
                    for widget in (self.details, self.log_text):
                        widget.configure(state='normal')
                        widget.delete('1.0', 'end')
                        widget.configure(state='disabled')
                elif kind == 'pause':
                    message, ready = value
                    self.root.deiconify()
                    self.status.set('任务已暂停，确认后继续。')
                    try:
                        messagebox.showinfo('任务暂停，确定后继续', message + '\n\n请处理游戏中的弹窗或恢复界面，'
                                            '再点击确定继续当前任务。按 F10 可结束任务。')
                    finally:
                        if not self.stop.is_set():
                            self.root.withdraw()
                        ready.set()
        except queue.Empty:
            pass
        self.tick_auto()
        self.root.after(100, self.poll)

    def pause_task(self, message):
        ready = threading.Event()
        self.events.put(('pause', (message, ready)))
        while not ready.wait(0.1):
            check_stop(self.stop)
        check_stop(self.stop)

    def take_frame(self):
        if self.busy or self.auto_enabled():
            return
        self.root.withdraw()

        def task():
            game = Game(self.stop)
            game.activate()
            game.wait(3)
            return game.frame()

        def done(frame):
            size = [frame.width, frame.height]
            if self.config.get('window_size') and self.config['window_size'] != size:
                self.config['points'], self.config['regions'], self.config['learned'] = {}, {}, {}
            self.config['window_size'] = size
            self.frame = frame
            self.photo = tk.PhotoImage(data=frame.ppm(), format='PPM')
            self.canvas.delete('all')
            self.canvas.create_image(0, 0, anchor='nw', image=self.photo)
            self.canvas.configure(scrollregion=(0, 0, frame.width, frame.height))
            self.render_roles()
            self.render_learned()
            self.status.set('选择左侧点位后点击截图；区域需点击左上角和右下角。菜单选项可展开后重新截图。')

        self.worker(task, done)

    def pick(self, event):
        if self.frame is None or self.busy or self.auto_enabled() or not self.role_list.curselection():
            return
        key = self.roles[self.role_list.curselection()[0]]
        point = [int(self.canvas.canvasx(event.x)), int(self.canvas.canvasy(event.y))]
        if not (0 <= point[0] < self.frame.width and 0 <= point[1] < self.frame.height):
            return
        if key in REGION_LABELS:
            if self.rect_start is None:
                self.rect_start = point
                self.status.set('再点击此区域的右下角。')
                return
            x, y = [min(self.rect_start[c], point[c]) for c in (0, 1)]
            width, height = [abs(self.rect_start[c] - point[c]) for c in (0, 1)]
            self.rect_start = None
            if min(width, height) < 4:
                return
            self.config['regions'][key] = [x, y, width, height]
            self.canvas.create_rectangle(x, y, x + width, y + height, outline='#39de91', width=2)
        else:
            self.config['points'][key] = point
            x, y = point
            self.canvas.create_line(x - 5, y, x + 5, y, fill='#ffcb40', width=2)
            self.canvas.create_line(x, y - 5, x, y + 5, fill='#ffcb40', width=2)
        if key != 'inventory_sort':
            self.config['learned'] = {}
        self.run_button.configure(state='disabled')
        index = self.role_list.curselection()[0]
        self.render_roles()
        self.render_learned()
        if index + 1 < len(self.roles):
            self.role_list.selection_clear(0, 'end')
            self.role_list.selection_set(index + 1)
            self.role_list.see(index + 1)
        self.status.set('整理按钮坐标已记录，保存后即可开始收菜。' if key == 'inventory_sort'
                        else '坐标已记录；点位修改后需重新记住两个界面。')

    def start_learn(self, mode):
        if self.busy or self.auto_enabled():
            return
        if not self.ready_empty.get() or not self.ready_backpack.get():
            messagebox.showinfo('记录界面', '请先清空九宫格、取消包含仓库物品，并勾选对应状态。合成界面需选择平时使用的配方。')
            return
        try:
            self.settings()
            config = copy.deepcopy(self.config)
        except Exception as error:
            messagebox.showerror('设置错误', str(error))
            return
        self.root.withdraw()

        def done(record):
            self.config['learned'][mode] = record
            self.save()
            self.render_learned()
            self.status.set('当前界面已记录。')
        self.worker(lambda: learn(config, mode, self.stop), done)

    def start_hover_test(self):
        if self.busy or self.auto_enabled():
            return
        try:
            self.save()
            validate(self.config)
        except Exception as error:
            messagebox.showerror('尚未完成校准', str(error))
            return
        config = copy.deepcopy(self.config)
        self.root.withdraw()

        def task():
            engine = Engine(config, self.stop, lambda text: self.events.put(('log', text)))
            try:
                return engine.test_hover()
            finally:
                engine.close()

        def done(item):
            self.render_items([item])
            self.tree.selection_set(str(item.slot))
            self.show_item(None)
            self.status.set('第一格测试：' + item.reason)
            if item.reason.startswith('未检测到'):
                messagebox.showinfo('未出现可识别详情',
                    '请确认第一格中有物品。若有物品，可把悬停等待调到 2 秒后重试。\n'
                    '本次没有点击或拖动物品，截图路径见下方详情。')
        self.worker(task, done)

    def start_return(self):
        if self.busy or self.auto_enabled():
            return
        try:
            self.settings()
            validate(self.config, execution=True)
        except Exception as error:
            messagebox.showerror('需要校准', str(error))
            return
        config = copy.deepcopy(self.config)
        self.root.withdraw()

        def task():
            engine = Engine(config, self.stop, lambda text: self.events.put(('log', text)))
            try:
                engine.return_to_inventory()
            finally:
                engine.close()

        self.worker(task, lambda result: self.status.set('已取回魔方物品，可继续开始收菜。'))

    def start_execution(self, *, automatic=False):
        if self.busy or (self.auto_enabled() and not automatic):
            return False
        try:
            self.save()
            validate(self.config, execution=True)
        except Exception as error:
            messagebox.showerror('配置错误', str(error))
            return False
        if not automatic and not messagebox.askokcancel('开始边扫边收菜',
                '腐蚀范围：' + '；'.join(CORROSION_TYPES[k] + '：' + '、'.join(v)
                    for k, v in self.config['corrosion_selection'].items() if v) + '\n'
                '自动查找所有 TaskbarHero 游戏进程，按 PID 顺序逐个完成收菜；各窗口沿用当前校准。\n'
                '多开时临时最小化其他队列窗口，本轮结束或停止后恢复原来的窗口状态。\n'
                '每个进程激活后先按一次空格，再开始收菜。\n'
                '开始时自动检查并打开魔方、仓库和背包页面，先整理背包再扫描。\n'
                '识别到目标就投入魔方，满9格或扫描结束执行腐蚀；不逐件复查。\n'
                '腐蚀结束后，剩余传奇、不朽装备各按同品质每9件合成，两个品质不混放。\n'
                '合成产物从中间格右键收回背包，整理后重扫，直到不足9件或达到合成批数上限。\n'
                '瘟疫之果不限品质腐蚀；供奉材料及其他指定高品质 Ctrl＋右键入库。\n'
                '铭刻材料：普通、罕见、稀有腐蚀，其余已确认品质入库，不受腐蚀勾选影响。\n'
                '仓库满页自动翻页，最后一页也满则从第一页复查；全部满仓跳过入库。\n'
                '每轮结束仅保留本轮各进程记录，清理更早的运行记录。\n'
                '添加失败跳过；异常弹窗确定后继续。锁定物品保留。\n\n按 F10 可停止。'):
            return False
        config = copy.deepcopy(self.config)
        self.root.withdraw()

        def task():
            folders = []
            self.events.put(('round_start', None))
            try:
                return harvest_all(config, self.stop, lambda text: self.events.put(('log', text)),
                                   lambda items: self.events.put(('items', items)), self.pause_task,
                                   on_record=folders.append)
            finally:
                try:
                    cleaned = retain_latest_runs(ROOT, folders)
                    if folders:
                        self.events.put(('log', f'仅保留本轮记录；清理旧记录 {cleaned.files} 个文件，'
                                         f'释放 {cleaned.bytes_freed / (1024 * 1024):.2f} MB。'))
                    for failure in cleaned.failures:
                        self.events.put(('log', failure))
                except Exception as error:
                    self.events.put(('log', '旧记录清理未完成：' + str(error)))

        def done(result):
            self.status.set(result.summary())
            if (automatic and self.auto_enabled() and not self.stop.is_set()
                    and not getattr(self, 'closing', False)):
                self.auto_harvest.finish()
            self.refresh_controls()
        self.worker(task, done)
        return True

    def render_items(self, items):
        self.display_items = items
        self.tree.delete(*self.tree.get_children())
        for item in items:
            self.tree.insert('', 'end', iid=str(item.slot), values=(item.slot + 1, ACTION_NAMES[item.action],
                item.quality or '未确认', KIND_NAMES[item.kind], item.level or '—',
                '整格' if item.action == 'store' else '—' if item.empty or not item.kind
                else item.count if item.count is not None else '未确认', item.reason))

    def show_item(self, event):
        if not self.tree.selection():
            return
        slot = int(self.tree.selection()[0])
        item = next(i for i in self.display_items if i.slot == slot)
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('end', item.text + '\n\n截图：' + item.image)
        self.details.configure(state='disabled')

    def open_runs(self):
        folder = ROOT / 'runs'
        folder.mkdir(exist_ok=True)
        os.startfile(folder)

    def start_diagnostics(self):
        if self.busy or self.auto_enabled():
            return
        self.status.set('正在只读检查多开进程、窗口、路径和会话……')

        def done(targets):
            self.status.set(f'诊断完成，当前可加入队列 {len(targets)} 个进程，详情见日志。')
            messagebox.showinfo('多开诊断', '诊断完成，未操作游戏。\n请查看下方日志及 harvest/process-diagnostics.txt。')

        self.worker(lambda: find_games(log=lambda text: self.events.put(('log', text))), done)

    def start_clear_cache(self):
        if self.busy or self.auto_enabled():
            return
        self.status.set('正在清理运行截图、日志和缓存……')

        def done(result):
            from vision import asset
            asset.cache_clear()
            self.render_items([])
            for widget in (self.details, self.log_text):
                widget.configure(state='normal')
                widget.delete('1.0', 'end')
                widget.configure(state='disabled')
            if result.failures:
                self.log_text.configure(state='normal')
                self.log_text.insert('end', '\n'.join(result.failures) + '\n')
                self.log_text.configure(state='disabled')
            self.status.set(result.summary())

        self.worker(lambda: clear_cache(ROOT), done)

    def close(self):
        self.closing = True
        self.request_stop()
        if self.busy:
            self.status.set('正在停止并释放鼠标，请稍候。')
            self.root.after(300, self.close)
        else:
            self.root.destroy()


def main():
    dpi_aware()
    root = tk.Tk()
    ttk.Style().theme_use('vista')
    App(root)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (ROOT / 'last-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
