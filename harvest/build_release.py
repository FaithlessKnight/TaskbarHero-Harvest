"""Build a ZIP with the assistant and a private, relocatable Python runtime."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parent
RELEASE = 'TaskbarHero-Harvest-1.49-TheFallen'
MODULES = (
    'app.py', 'auto_harvest.py', 'cache.py', 'cube_modes.py', 'engine.py',
    'inventory.py', 'panels.py', 'rules.py', 'sessions.py', 'streaming.py',
    'vision.py', 'warehouse_pages.py', 'windows_io.py', 'process_diagnostics.py', 'game_paths.py',
    'window_isolation.py', 'tooltip_regions.py', 'tooltip_ocr.py', 'window_restore.py',
    'supplemental.py', 'config_transfer.py', 'preferences_ui.py',
)
GUIDE = '''TaskbarHero 收菜助手 1.49
作者：TheFallen

使用方法
1. 请完整解压本 ZIP，不要在压缩包内运行。
2. 将包内整个 harvest 文件夹放到游戏安装目录，与 TaskBarHero.exe 同级。
3. 关闭旧助手后，双击 harvest\\Start.cmd。
4. 本包附带 Python 3.14 与 Tkinter，无需另行安装 Python。
   仍需 Windows 简体中文 OCR 组件；本机已使用的 OCR 环境可继续使用。

本包使用空白默认配置；首次使用需校准。不包含个人设置、游戏本体、运行记录、缓存或测试截图。
若使用不同电脑或窗口尺寸、缩放、面板布局发生变化，请重新校准。
更新已有安装时，先保留目标电脑原有的 harvest\\config.json；替换程序文件时不要覆盖它。
多开包含 Sandboxie 实例时，请在沙盒外正常启动助手。
支持默认 Sandboxie 路径 X:\\Sandbox\\用户名\\沙盒名\\drive\\盘符\\原游戏路径，按映射前的完整安装目录核对。
若只识别到部分进程，点击“多开诊断”，或双击 harvest\\Diagnose.cmd。
报告保存在 harvest\\process-diagnostics.txt，逐个 PID 显示窗口、路径、会话和访问失败原因。
不同会话需在各自会话内运行助手；权限不一致时可在沙盒外以管理员运行后重试。
诊断只读，不会激活游戏、按空格或执行收菜。
每个进程完成收菜后尝试收起页面；按钮重试后仍无法确认时保存截图、保留页面并继续队列。
点击收起后只在原按钮附近确认展开箭头，避免其他相同箭头干扰；暂停续跑不会点击已展开方向的按钮。
启动时遇到多个展开按钮，先对照本轮前一进程已确认的按钮位置，再悬停核对当前窗口归属。
多开时先保存窗口状态，只显示当前待处理游戏，临时最小化其他队列窗口，防止透明区域截入另一局游戏。
本轮结束、F10 停止或异常退出任务后恢复原窗口位置及最小化、最大化状态；自动等待期间也恢复。
无法唯一确认时保存实际游戏截图并记录候选坐标，不会直接点击另一个进程的按钮。
装饰材料支持直接识别提示框头部的罕见、稀有字形及材料类型，兼容小字被 OCR 漏读。
铭刻材料单独处理：普通、罕见、稀有优先腐蚀；其他已确认品质（传奇、不朽、至宝等）优先入库。
铭刻材料规则不受腐蚀勾选影响；锁定和品质冲突仍保留；腐蚀目标仅数量未确认时按单次添加处理。
提示框先按标题边框还原裁剪，避免漏掉品质和材料类型；新增罕见、超凡字形及法球主属性识别。
提示框远离鼠标或被屏幕底部截断时，仅在头部图标与背包原格图标唯一对应后读取；普通对比框仍优先靠近鼠标的详情。
装备图标受 Lv 等级文字遮挡时，额外核对图标上部两块细节，要求共同位移唯一对应，避免漏掉远处装备详情。
新增头部攻击伤害字形识别；不会用装备名称或下方属性描述猜测类型。
无法确认提示框时保存 tooltip-unmatched.bmp 与同名 JSON，记录候选区域及来源图标核对结果。
识别到物品详情远离鼠标时，自动打开设置 → 图形 → 恢复窗口位置右侧恢复 → 设置右上角关闭。
仅点击 Shift+F12 的恢复窗口位置一行，不点击 Shift+F11 的恢复窗口缩小。
关闭设置后重新定位背包、仓库和魔方，确认物品及功能未变，在本次任务内同步坐标后继续当前物品。
不改写保存的校准。持续异常时不反复打开设置；重新出现正常近处详情后才能再次触发恢复。
恢复流程中 F10 可停止；暂停续跑先完成关闭设置和页面确认，已发送的恢复按钮不重复点击。
入库确认可单独核对已变化并匹配来源图标的接收格，避免仓库其他图标动画阻断确认；仍要求背包原格清空、魔方未变。
入库未确认时保存同名 BMP 和 JSON 状态，不重复发送入库快捷键。
品质或类型未读清时，单独截取头部进行 4 倍、5 倍平滑放大补读，避免长提示框限制小字放大倍率。
补读仅采用两次一致的完整字段；品质或材料类型冲突仍保留。已确认物品不增加 OCR 或鼠标等待。
系统分辨率与远程画面的显示大小并不等同；诊断 JSON 记录实际游戏及提示框像素尺寸。
改动已用本机保存图片验证，未确认低分辨率电脑的具体成因；更换窗口尺寸或游戏缩放仍需在目标电脑重新校准。
自动收菜默认关闭，需手动点击开启。F10 可以停止任务。
新增“补充物品与配置”页，按完整名称、类型和品质范围指定放入魔方腐蚀或放入仓库。
补充规则优先于默认处理；锁定、已装备对比及识别冲突仍保留。
可在识别清单选中物品后点“补充选中物品”，核对名称再新增；支持载入编辑、单选、多选及全选删除。
配置导入、导出使用 JSON；常规设置始终包含，可分别勾选是否包含窗口校准与补充物品。
窗口校准包含行列数、点位、区域和已记录界面。导入替换勾选且文件包含的部分，其余保留。
导入补充物品会替换当前全部补充规则（不是追加）；确认框会列出实际替换范围与数量。
任务执行或自动等待期间不能编辑补充规则或导入导出，请先停止任务。
清理缓存不删除补充规则或配置；导入配置不会自动开启收菜。
识别到腐蚀目标后，按已确认数量执行 Alt＋右键（如 3 个添加 3 次），每次确认新增一格再继续。
同一叠材料多个选中格共享背包原数量，执行后按整叠减少量核对，不重复计算消耗。
魔方剩余格数不足时先放满执行，整理后重扫余量；添加异常取回当前物品本次投料并跳过。
若实际一次已移入整叠，则按已移入数量停止补点，避免继续点击空背包格。
补充名称漏字或两次不一致时，去除标题边框并分离彩色字形，在两种倍率下补读完整名称；不会用近似名称匹配。
新增黑钢锭截图中的完整“超凡级”字形识别，确保命中用户设置的超凡品质范围。
已经确认腐蚀、但堆叠数量未确认的材料，只 Alt＋右键一次；新增一格确认后参与本批腐蚀。
该单次投料不要求补读背包和魔方数量；执行后以选中格清空确认结束，再整理重扫余量。
若执行后选中格持续未清空则等待或暂停，不盲目重复点击执行；F10 仍可停止。
修复古树树液提示框的“稀有级”漏读：增加该品质行与“雕刻材料”的完整字形识别。
按已勾选的稀有材料腐蚀范围处理；补充规则若要单独指定此物品，应填写“古树树液”，与“古树树叶”区分。
字形仅在当前提示框品质和类型区域匹配；名称、属性说明不能提供品质或类型，冲突和锁定仍保留。

目录结构
  使用说明.txt
  SHA256SUMS.txt                 文件校验
  harvest\\Start.cmd             启动入口
  harvest\\README.md             完整功能说明
  harvest\\config.json           首次使用的默认配置
  harvest\\assets\\               识别模板
  harvest\\runtime\\              随包 Python / Tk 运行环境及其许可
'''


def build():
    runtime = Path(sys.base_prefix)
    destination = ROOT.parent / 'dist' / (RELEASE + '.zip')
    destination.parent.mkdir(exist_ok=True)
    sources = [(ROOT / name, 'harvest/' + name) for name in MODULES]
    sources.extend((ROOT / name, 'harvest/' + name)
                   for name in ('Start.cmd', 'Start.ps1', 'Diagnose.cmd', 'ocr.ps1', 'README.md'))
    sources.append((ROOT / 'config.example.json', 'harvest/config.json'))
    sources.extend((path, 'harvest/' + path.relative_to(ROOT).as_posix())
                   for path in sorted((ROOT / 'assets').rglob('*')) if path.is_file())
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python314.dll',
                 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        sources.append((runtime / name, 'harvest/runtime/' + name))
    for folder in ('Lib', 'DLLs', 'tcl'):
        for path in sorted((runtime / folder).rglob('*')):
            relative = path.relative_to(runtime)
            if not path.is_file() or path.suffix in ('.pyc', '.pyo'):
                continue
            if {'__pycache__', 'site-packages', 'test', 'tests', 'idlelib', 'turtledemo'} & set(relative.parts):
                continue
            sources.append((path, 'harvest/runtime/' + relative.as_posix()))
    for name in MODULES:
        ast.parse((ROOT / name).read_text(encoding='utf-8'), filename=name)
    json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
    records = []
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, name in sources:
            data = source.read_bytes()
            archive.writestr(name, data)
            records.append(hashlib.sha256(data).hexdigest() + '  ' + name)
        guide = GUIDE.encode('utf-8-sig')
        archive.writestr('使用说明.txt', guide)
        records.append(hashlib.sha256(guide).hexdigest() + '  使用说明.txt')
        archive.writestr('SHA256SUMS.txt', '\n'.join(records) + '\n')
    with zipfile.ZipFile(destination) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError('ZIP 校验失败：' + bad)
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError('ZIP 出现重复文件')
        if any('/runs/' in name or '/__pycache__/' in name or '/site-packages/' in name for name in names):
            raise RuntimeError('ZIP 包含缓存或非运行依赖')
    print(json.dumps({'zip': str(destination), 'files': len(names), 'bytes': destination.stat().st_size,
                      'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == '__main__':
    build()
