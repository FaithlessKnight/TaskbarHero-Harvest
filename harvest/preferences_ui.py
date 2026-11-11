"""User supplied item routes and selective configuration transfer."""
import copy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config_transfer import export_data, load_file, merge_config, save_file
from supplemental import QUALITIES, new_rule, normalize_rules, title_text


KINDS = {'装备': 'equipment', '材料': 'material'}
ACTIONS = {'放入魔方腐蚀': 'corrode', '放入仓库': 'store'}


class PreferencesUI:
    def __init__(self, app):
        self.app = app
        self.frame = ttk.Frame(app.book, padding=12)
        app.book.add(self.frame, text='补充物品与配置')
        self.controls = []
        self.editing = None
        self.name = tk.StringVar()
        self.kind = tk.StringVar(value='材料')
        self.action = tk.StringVar(value='放入魔方腐蚀')
        self.all_qualities = tk.BooleanVar(value=True)
        self.qualities = {q: tk.BooleanVar(value=False) for q in QUALITIES}
        hint = ('按提示框中的完整名称匹配，补充规则优先于默认处理规则。锁定、已装备对比及识别冲突仍保留。\n'
                '名称需两次识别一致；名称未读清时仍按默认规则处理。“全部品质”也适用于品质未读出的物品。')
        ttk.Label(self.frame, text=hint, wraplength=920).pack(anchor='w', pady=(0, 10))
        editor = ttk.LabelFrame(self.frame, text='新增 / 编辑补充物品', padding=10)
        editor.pack(fill='x')
        line = ttk.Frame(editor)
        line.pack(fill='x')
        ttk.Label(line, text='完整名称').pack(side='left')
        entry = ttk.Entry(line, textvariable=self.name, width=28)
        entry.pack(side='left', padx=(6, 12), fill='x', expand=True)
        self.controls.append((entry, 'normal'))
        for label, variable, values, width in (('类型', self.kind, KINDS, 7), ('处理', self.action, ACTIONS, 16)):
            ttk.Label(line, text=label).pack(side='left', padx=(0, 6))
            box = ttk.Combobox(line, textvariable=variable, values=list(values), state='readonly', width=width)
            box.pack(side='left', padx=(0, 12))
            self.controls.append((box, 'readonly'))
        scope = ttk.Frame(editor)
        scope.pack(fill='x', pady=8)
        all_box = ttk.Checkbutton(scope, text='全部品质', variable=self.all_qualities, command=self.refresh)
        all_box.pack(side='left', padx=(0, 12))
        self.controls.append((all_box, 'normal'))
        self.quality_boxes = []
        for q, var in self.qualities.items():
            box = ttk.Checkbutton(scope, text=q, variable=var)
            box.pack(side='left', padx=(0, 6))
            self.quality_boxes.append(box)
        buttons = ttk.Frame(editor)
        buttons.pack(fill='x')
        self.button(buttons, '新增规则', self.add)
        self.button(buttons, '载入选中规则', self.edit)
        self.button(buttons, '保存修改', self.update)
        self.button(buttons, '清空输入', self.clear)
        self.button(buttons, '从当前识别记录补充', self.from_record)
        area = ttk.Frame(self.frame)
        area.pack(fill='both', expand=True, pady=10)
        self.tree = ttk.Treeview(area, columns=('name', 'kind', 'qualities', 'action'), show='headings', selectmode='extended', height=8)
        for key, label, width in (('name', '完整物品名称', 300), ('kind', '类型', 80),
                                  ('qualities', '品质范围', 300), ('action', '处理方式', 150)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=70)
        self.tree.pack(side='left', fill='both', expand=True)
        bar = ttk.Scrollbar(area, orient='vertical', command=self.tree.yview)
        bar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind('<Double-1>', lambda event: self.edit())
        self.tree.bind('<Control-a>', self.select_all)
        self.tree.bind('<Delete>', lambda event: self.delete())
        actions = ttk.Frame(self.frame)
        actions.pack(fill='x')
        self.button(actions, '全选', self.select_all)
        self.button(actions, '取消选择', lambda: self.tree.selection_remove(self.tree.selection()))
        self.button(actions, '删除选中（支持多选）', self.delete)
        self.summary = tk.StringVar()
        ttk.Label(actions, textvariable=self.summary).pack(side='left', padx=12)
        ttk.Label(self.frame, text='按住 Ctrl 可逐条多选，按住 Shift 可连续选择；全选后点“删除选中”即可全部删除。').pack(anchor='w', pady=(6, 12))
        transfer = ttk.LabelFrame(self.frame, text='配置导入 / 导出', padding=10)
        transfer.pack(fill='x')
        self.include_calibration = tk.BooleanVar(value=False)
        self.include_items = tk.BooleanVar(value=True)
        row = ttk.Frame(transfer)
        row.pack(fill='x')
        for label, variable in (('包含窗口校准', self.include_calibration), ('包含补充物品', self.include_items)):
            box = ttk.Checkbutton(row, text=label, variable=variable)
            box.pack(side='left', padx=(0, 16))
            self.controls.append((box, 'normal'))
        self.button(row, '导出配置…', self.export_config)
        self.button(row, '导入配置…', self.import_config)
        ttk.Label(transfer, text='常规设置始终包含。校准包含行列数、点位、区域和已记录界面。\n'
                  '导入会替换所选且文件中包含的部分；未勾选或文件未包含的部分保持原样。', wraplength=900).pack(anchor='w', pady=(6, 0))
        self.render()
        self.refresh()

    def button(self, parent, label, command):
        button = ttk.Button(parent, text=label, command=command)
        button.pack(side='left', padx=(0, 8))
        self.controls.append((button, 'normal'))
        return button

    def locked(self):
        return self.app.busy or self.app.auto_enabled()

    def refresh(self):
        locked = self.locked()
        for control, state in self.controls:
            control.configure(state='disabled' if locked else state)
        for box in self.quality_boxes:
            box.configure(state='disabled' if locked or self.all_qualities.get() else 'normal')

    def render(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.app.config.get('supplemental_items', [])
        for row in rows:
            self.tree.insert('', 'end', iid=row['id'], values=(row['name'],
                next(k for k, v in KINDS.items() if v == row['kind']), '、'.join(row['qualities']) or '全部品质',
                next(k for k, v in ACTIONS.items() if v == row['action'])))
        self.summary.set(f'共 {len(rows)} 条补充规则（修改后自动保存）')

    def clear(self):
        if self.locked():
            return
        self.editing = None
        self.name.set('')
        self.kind.set('材料')
        self.action.set('放入魔方腐蚀')
        self.all_qualities.set(True)
        for var in self.qualities.values():
            var.set(False)
        self.refresh()

    def form_rule(self):
        if not self.all_qualities.get() and not any(v.get() for v in self.qualities.values()):
            raise ValueError('请选择至少一种品质，或勾选“全部品质”。')
        return new_rule(self.name.get(), KINDS[self.kind.get()], ACTIONS[self.action.get()],
                        [] if self.all_qualities.get() else [q for q, v in self.qualities.items() if v.get()])

    def persist(self, rows):
        candidate = copy.deepcopy(self.app.config)
        candidate['supplemental_items'] = normalize_rules(rows)
        self.app.write_config(candidate)
        self.app.config = candidate
        self.render()

    def add(self):
        if self.locked():
            return
        try:
            row = self.form_rule()
            self.persist(self.app.config.get('supplemental_items', []) + [row])
            self.tree.selection_set(row['id'])
            self.app.status.set('补充物品已保存，下次收菜按新规则处理。')
        except (ValueError, OSError, KeyError, tk.TclError) as error:
            messagebox.showerror('新增失败', str(error), parent=self.app.root)

    def edit(self):
        if self.locked():
            return
        selected = self.tree.selection()
        if len(selected) != 1:
            messagebox.showinfo('编辑规则', '请选择一条规则后载入。', parent=self.app.root)
            return
        row = next(r for r in self.app.config['supplemental_items'] if r['id'] == selected[0])
        self.editing = row['id']
        self.name.set(row['name'])
        self.kind.set(next(k for k, v in KINDS.items() if v == row['kind']))
        self.action.set(next(k for k, v in ACTIONS.items() if v == row['action']))
        self.all_qualities.set(not row['qualities'])
        for q, var in self.qualities.items():
            var.set(q in row['qualities'])
        self.refresh()
        self.app.status.set('正在编辑：' + row['name'] + '；修改后点击“保存修改”。')

    def update(self):
        if self.locked():
            return
        if not self.editing:
            messagebox.showinfo('编辑规则', '先选择一条规则并点击“载入选中规则”。', parent=self.app.root)
            return
        try:
            row = dict(self.form_rule(), id=self.editing)
            rows = self.app.config.get('supplemental_items', [])
            if not any(r['id'] == self.editing for r in rows):
                raise ValueError('原规则已删除，请重新新增。')
            self.persist([row if r['id'] == self.editing else r for r in rows])
            self.tree.selection_set(row['id'])
            self.app.status.set('补充规则修改已保存。')
        except (ValueError, OSError, KeyError, tk.TclError) as error:
            messagebox.showerror('修改失败', str(error), parent=self.app.root)

    def select_all(self, event=None):
        self.tree.selection_set(self.tree.get_children())
        return 'break'

    def delete(self):
        if self.locked():
            return
        selected = set(self.tree.selection())
        if not selected:
            return
        if not messagebox.askyesno('删除补充物品', f'删除选中的 {len(selected)} 条补充规则？', parent=self.app.root):
            return
        try:
            self.persist([r for r in self.app.config.get('supplemental_items', []) if r['id'] not in selected])
            if self.editing in selected:
                self.clear()
            self.app.status.set(f'已删除 {len(selected)} 条补充规则。')
        except (ValueError, OSError) as error:
            messagebox.showerror('删除失败', str(error), parent=self.app.root)

    def from_record(self):
        if self.locked():
            return
        selected = self.app.tree.selection()
        item = next((i for i in self.app.display_items if selected and str(i.slot) == selected[0]), None)
        if item is None or item.empty:
            messagebox.showinfo('补充物品', '请先在“收菜与记录”的识别清单中选中一个物品。', parent=self.app.root)
            return
        self.clear()
        self.name.set(item.name or next((title_text(s) for s in item.text.splitlines() if title_text(s)), ''))
        self.kind.set('装备' if item.kind == 'equipment' else '材料')
        self.action.set('放入仓库' if item.action == 'store' else '放入魔方腐蚀')
        if item.quality in self.qualities:
            self.all_qualities.set(False)
            self.qualities[item.quality].set(True)
        self.app.book.select(self.frame)
        self.refresh()
        self.app.status.set('已填入识别记录，请核对完整名称、类型、品质和处理方式，再点击“新增规则”。')

    def export_config(self):
        if self.locked():
            return
        try:
            self.app.settings()
            data = export_data(self.app.config, include_calibration=self.include_calibration.get(),
                               include_items=self.include_items.get())
            path = filedialog.asksaveasfilename(parent=self.app.root, title='导出收菜配置',
                defaultextension='.json', initialfile='TaskbarHero-配置.json', filetypes=[('配置文件', '*.json')])
            if path:
                save_file(path, data)
                self.app.status.set('配置已导出：' + path)
        except (ValueError, OSError, tk.TclError) as error:
            messagebox.showerror('导出失败', str(error), parent=self.app.root)

    def import_config(self):
        if self.locked():
            return
        path = filedialog.askopenfilename(parent=self.app.root, title='导入收菜配置', filetypes=[('配置文件', '*.json')])
        if not path:
            return
        try:
            data = load_file(path)
            use_cal = self.include_calibration.get() and 'calibration' in data
            use_items = self.include_items.get() and 'supplemental_items' in data
            parts = ['常规设置（腐蚀勾选、悬停、执行间隔、自动间隔、合成批数）']
            if use_cal:
                parts.append('窗口校准（包括行列数、点位和已记录界面）')
            if use_items:
                parts.append(f'全部补充规则（替换为文件中的 {len(data["supplemental_items"])} 条）')
            missing = []
            if self.include_calibration.get() and 'calibration' not in data:
                missing.append('文件未包含窗口校准，保留当前校准。')
            if self.include_items.get() and 'supplemental_items' not in data:
                missing.append('文件未包含补充物品，保留当前规则。')
            prompt = '将替换以下内容：\n\n' + '\n'.join('• ' + p for p in parts)
            prompt += '\n\n' + '\n'.join(missing) + '\n未选择的部分保持原样。是否导入？'
            if not messagebox.askyesno('确认导入范围', prompt, parent=self.app.root):
                return
            candidate = merge_config(self.app.config, data, include_calibration=use_cal, include_items=use_items)
            self.app.write_config(candidate)
            self.app.config = candidate
            self.app.reload_settings()
            if use_cal:
                self.app.frame = None
                self.app.rect_start = None
                self.app.canvas.delete('all')
                self.app.ready_empty.set(False)
                self.app.ready_backpack.set(False)
            self.app.render_roles()
            self.app.render_learned()
            self.app.render_items([])
            self.clear()
            self.render()
            self.app.refresh_controls()
            self.app.status.set('配置已导入并保存。自动收菜保持关闭；更换电脑或布局后请核对校准。')
        except (ValueError, OSError, tk.TclError) as error:
            messagebox.showerror('导入失败', str(error), parent=self.app.root)
