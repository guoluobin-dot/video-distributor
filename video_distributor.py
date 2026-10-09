# -*- coding: utf-8 -*-
"""视频/图片分发工具 - 霓虹赛博朋克版（完全本地运行）
选输出父文件夹 -> 投放视频/图片(含子文件夹) -> 按子文件夹名+文件名自然排序
-> 按起始日期+每组数量+间隔天数 生成 MM-DD未分发 -> 预览确认后移动，可撤回，可改名
"""
import os
import re
import csv
import json
import glob
import shutil
import math
import random
import subprocess
import sys
import tempfile
import threading
import time
import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except Exception:
    HAS_DND = False

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".m4v", ".webm", ".mpg", ".mpeg", ".3gp", ".ts"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif"}
ALL_EXTS = VIDEO_EXTS | IMAGE_EXTS
CONFIG_NAME = "video_distributor_config.json"
APP_VERSION = "v2.8-多目标均分"


def build_stamp():
    """构建时间 = 当前跑的这份源码/exe 的文件修改时间。
    标题栏直接可见，一眼分清界面跑的是不是最新打包的 exe。"""
    try:
        p = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
        return datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d %H:%M")
    except Exception:
        return "?"
DUB_BACKUP_PREFIX = ".配乐原片备份_"

# ---------- 小清新配色 ----------
BG = "#EAF6F6"
PANEL = "#FFFFFF"
PANEL2 = "#DFF3F3"
ENTRY_BG = "#FFFFFF"
TEXT = "#2F4858"
MUTED = "#7AA5B8"
SEA_BLUE = "#4BB4C7"
SEA_DEEP = "#2A7F9D"
SAND = "#FFF6E9"
CORAL = "#FF8A7A"
SUN = "#FFC94D"
# 兼容旧霓虹变量名（避免改动所有调用处）
NEON_CYAN = SEA_DEEP
NEON_PINK = CORAL
NEON_YELLOW = "#B7791F"
NEON_PURPLE = SEA_BLUE
BTN_BG = "#FFFFFF"
FONT_MAIN = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 11, "bold")


def get_allowed_exts(media_type):
    if media_type == "video":
        return VIDEO_EXTS
    if media_type == "image":
        return IMAGE_EXTS
    return ALL_EXTS


def rename_word_for(media_type):
    if media_type == "video":
        return "视频"
    if media_type == "image":
        return "图片"
    return "文件"


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_config_dir():
    """配置目录：优先 %APPDATA%/VideoDistributor（Program Files下可写），
    失败回退到程序目录（便携/旧行为）"""
    try:
        appdata = os.environ.get("APPDATA")
        if appdata:
            p = os.path.join(appdata, "VideoDistributor")
            os.makedirs(p, exist_ok=True)
            return p
    except Exception:
        pass
    return get_app_dir()


def _config_candidates():
    """读配置的候选路径：新路径优先，旧的程序目录兜底（换电脑拷贝兼容）"""
    seen = []
    for d in (get_config_dir(), get_app_dir()):
        try:
            p = os.path.join(d, CONFIG_NAME)
            if p not in seen:
                seen.append(p)
        except Exception:
            continue
    return seen


def load_config():
    for p in _config_candidates():
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                continue
    return {}


def save_config(cfg):
    # 主写 %APPDATA%，便携场景再顺手写一份到程序目录（写失败静默忽略）
    dirs = []
    try:
        dirs.append(get_config_dir())
    except Exception:
        pass
    try:
        appd = get_app_dir()
        if not dirs or os.path.normcase(os.path.normpath(appd)) != os.path.normcase(os.path.normpath(dirs[0])):
            dirs.append(appd)
    except Exception:
        pass
    for d in dirs:
        tmp = None
        try:
            p = os.path.join(d, CONFIG_NAME)
            # 先写临时文件再原子替换：原来直接 open(p,"w") 会先清空，
            # 写到一半断电/磁盘满就留下半截 json，下次启动设置全丢且用户毫无提示
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp, p)
            tmp = None
        except Exception:
            pass
        finally:
            if tmp:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
            continue
        # 首选路径写成功即停？不：为了便携继续写第二个，失败忽略即可
    return


def natsort_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def safe_int_var(var, default):
    """安全读取 IntVar：Entry 被清空/输字母时 Tk 会抛 TclError，返回 default 而不是崩溃。"""
    try:
        v = var.get()
        return int(v)
    except Exception:
        return default


def collect_files(paths, allowed_exts=None):
    """输入文件/文件夹列表，递归收集允许的媒体文件绝对路径。
    跳过 .__dub_/__hold_ 这类上次崩溃残留的临时半成品，避免投放垃圾。"""
    allowed = allowed_exts or ALL_EXTS
    allowed = {e.lower() for e in allowed}
    out = []
    for p in paths:
        p = p.strip().strip('"').strip("'")
        if not p or not os.path.exists(p):
            continue
        if os.path.isfile(p):
            bn = os.path.basename(p)
            if bn.startswith(".__dub_") or bn.startswith(".__hold_"):
                continue
            if os.path.splitext(p)[1].lower() in allowed:
                out.append(os.path.abspath(p))
        else:
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    if fn.startswith(".__dub_") or fn.startswith(".__hold_"):
                        continue
                    if os.path.splitext(fn)[1].lower() in allowed:
                        out.append(os.path.abspath(os.path.join(root, fn)))
    seen = set()
    uniq = []
    for x in out:
        # Windows 不区分大小写，归一化后再去重，避免 C:\A.mp4 与 c:\a.mp4 重复投放
        k = os.path.normcase(os.path.normpath(x))
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


def collect_videos(paths, allowed_exts=None):
    # 兼容旧测试：默认只收视频
    return collect_files(paths, allowed_exts or VIDEO_EXTS)


def sort_files(paths):
    """按完整所在目录自然排序，再按文件名自然排序。
    原来只看直接父文件夹名，多层嵌套时（如 组1/素材A 与 组2/素材A）会交错；
    改按完整目录比较后，单层行为不变（公共前缀相同，比的还是最后那级），多层时大组顺序也正确。"""
    def key(p):
        try:
            d = os.path.dirname(os.path.abspath(p))
        except Exception:
            d = os.path.dirname(p)
        base = os.path.basename(p)
        return (natsort_key(d.lower()), natsort_key(base), p.lower())
    return sorted(paths, key=key)


def sort_videos(paths):
    return sort_files(paths)


def folder_name(dt):
    return f"{dt.month:02d}-{dt.day:02d}未分发"


MAX_INTERVAL_DAYS = 3650   # 约10年；再大就夹住，避免 datetime.timedelta 溢出把程序搞崩


def generate_dates(start_date, n_groups, interval_days):
    step = max(1, min(MAX_INTERVAL_DAYS, int(interval_days)) + 1)  # 0=连续每天,1=隔1天(8号->10号)
    return [start_date + datetime.timedelta(days=i * step) for i in range(n_groups)]


def build_plan(file_count, per_group, start_date, interval_days):
    if file_count <= 0 or per_group <= 0:
        return []
    n = math.ceil(file_count / per_group)
    dates = generate_dates(start_date, n, interval_days)
    plan = []
    left = file_count
    for d in dates:
        c = min(per_group, left)
        plan.append({"date": d, "count": c})
        left -= c
    return plan


def allocate(files_sorted, plan):
    res = []
    idx = 0
    for row in plan:
        c = int(row["count"])
        chunk = files_sorted[idx:idx + c]
        if not chunk:
            break
        res.append((row["date"], folder_name(row["date"]), chunk))
        idx += len(chunk)
    return res


def _pk(p):
    """路径比较用的归一化 key：Windows 不区分大小写/分隔符，撤回链和移动判断必须按它比，
    否则同一路径大小写或斜杠写法不同就会被当成两个文件。"""
    try:
        return os.path.normcase(os.path.normpath(str(p)))
    except Exception:
        return str(p)


_uniq_counter = {"n": 0}
_uniq_lock = threading.Lock()

def _next_uniq():
    with _uniq_lock:
        _uniq_counter["n"] += 1
        return _uniq_counter["n"]


def unique_dest(dest_path, limit=1000, used=None):
    """生成一个保证不存在的同目录新名字。
    used：本次批处理已占用的路径集合（可为空）。磁盘上还不存在但本批已占用的名字
    也必须避开，否则一批里两个文件会规划到同一个目标。
    返回的名字一定经过存在性校验——校验不过会抛错，绝不返回一个可能覆盖别人的名字。"""
    used = used or ()

    def _free(p):
        return not os.path.exists(p) and _pk(p) not in used

    if _free(dest_path):
        return dest_path
    base, ext = os.path.splitext(dest_path)
    for i in range(1, limit + 1):
        cand = f"{base}_{i}{ext}"
        if _free(cand):
            return cand
    # 极端情况：加时间戳+进程+自增。计数器必须带上，否则同一秒内两次调用会算出同一个名字。
    stamp = datetime.datetime.now().strftime("%H%M%S")
    for k in range(limit):
        cand = f"{base}_{stamp}_{os.getpid()}_{_next_uniq()}{ext}"
        if _free(cand):
            return cand
    raise OSError(f"无法生成不重名的文件名：{dest_path}")


def unique_dest_seen(dest_path, used, limit=1000):
    """同一批里去重：既看磁盘，也看本次已占用的路径集合。"""
    return unique_dest(dest_path, limit, used)


def ensure_tail_group(files_sorted, allocs, plan, start_date_getter, interval):
    """preview/execute 共用：计划总量不够时自动顺延多建一组装剩下。
    返回 (allocs, appended_bool)。原来三处重复代码，抽到一起防改漏。"""
    used = sum(len(c) for _, _, c in allocs)
    if used >= len(files_sorted):
        return allocs, False
    rest = files_sorted[used:]
    try:
        inv = max(0, min(MAX_INTERVAL_DAYS, int(interval)))
    except Exception:
        inv = 0
    try:
        if allocs:
            last_date = allocs[-1][0] + datetime.timedelta(days=inv + 1)
        else:
            last_date = start_date_getter()
    except Exception:
        last_date = datetime.date.today()
    allocs = list(allocs) + [(last_date, folder_name(last_date), rest)]
    return allocs, True


def _rename_word_for_file(rename_word, src):
    """分发改名时的 per-file 词：rename_word='auto'（视频+图片混排）时按扩展名区分，
    与“重命名已分发”的 auto 行为一致，避免分发时全叫“文件”、事后又叫“视频/图片”的不一致。"""
    if rename_word == "auto":
        try:
            return "视频" if os.path.splitext(src)[1].lower() in VIDEO_EXTS else "图片"
        except Exception:
            return "文件"
    return rename_word


def make_throttled_progress(fn, min_gap_ms=120):
    """进度节流：execute_move 每个文件回调一次，直接转发就是每个文件一次
    跨线程 Tk 调用（几千个文件 = 几千次往返，会明显拖慢移动）。"""
    state = {"last": 0.0}
    lock = threading.Lock()

    def _p(done):
        try:
            now = time.time() * 1000.0
            with lock:
                if now - state["last"] < min_gap_ms:
                    return
                state["last"] = now
            fn(done)
        except Exception:
            pass
    return _p


def execute_move(allocations, output_root, rename=False, rename_word="视频", progress=None):
    """progress(done) 每处理一个文件回调一次（在工作线程里调，回调自己负责回主线程）。
    rename_word='auto' 时按每个文件的扩展名决定叫视频还是图片。
    单个文件出错只记 FAILED 行，不会中断整批（原来异常会冒出去让整轮白跑）。"""
    moved = 0
    rows = []
    done = 0
    for dt, fname, files in allocations:
        target_dir = os.path.join(output_root, fname)
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            for src in files:
                rows.append((src, f"FAILED:建目录失败:{e}", fname))
            continue
        for i, src in enumerate(files, 1):
            done += 1
            if progress:
                try:
                    progress(done)
                except Exception:
                    pass
            try:
                if not os.path.exists(src):
                    rows.append((src, "SKIPPED:源文件不存在", fname))
                    continue
                # 路径比较必须 normcase：Windows 大小写/斜杠不敏感，
                # 否则“已在目标文件夹”判断失效，会把已分发的文件改名成 xxx_1
                if _pk(os.path.dirname(src)) == _pk(target_dir) and not rename:
                    # 已在正确文件夹且不改名：算已分发，从待分列表移除，但不计 moved、不写有效迁移
                    rows.append((src, "SKIPPED:已在目标文件夹", fname))
                    continue
                ext = os.path.splitext(src)[1]
                if rename:
                    w = _rename_word_for_file(rename_word, src)
                    new_base = f"{fname}{w}{i}{ext}"
                    dest = os.path.join(target_dir, new_base)
                    if os.path.exists(dest):
                        dest = unique_dest(dest)
                else:
                    dest = os.path.join(target_dir, os.path.basename(src))
                    if os.path.exists(dest):
                        dest = unique_dest(dest)
                if _pk(src) == _pk(dest):
                    rows.append((src, "SKIPPED:同路径", fname))
                    continue
                src_size = os.path.getsize(src) if os.path.isfile(src) else -1
                shutil.move(src, dest)
                # 跨盘 move 是 copy+delete：校验目标存在且大小>0，否则记失败
                if not os.path.exists(dest) or (src_size > 0 and os.path.getsize(dest) == 0):
                    rows.append((src, f"FAILED:移动后校验失败(dest={dest})", fname))
                    continue
                moved += 1
                rows.append((src, dest, fname))
            except Exception as e:
                # 关键：move 报异常时可能“其实已经搬过去了”（例如源删不掉）。
                # 目标确实存在就记成一次真实迁移，撤回才能找回；否则记 FAILED。
                try:
                    if _pk(src) != _pk(dest) and os.path.exists(dest) and not os.path.exists(src):
                        moved += 1
                        rows.append((src, dest, fname))
                        continue
                except Exception:
                    pass
                rows.append((src, f"FAILED:{e}", fname))
    return moved, rows


_BAD_ROW_PREFIX = ("FAILED", "SKIPPED", "CANCELLED", "PARTIAL")


def load_dub_maps(output_root):
    """读所有配乐日志，返回 {配乐前路径: (配乐后路径, 备份原片路径或None, 日志路径)}。
    跳过 FAILED/SKIPPED/CANCELLED 行和 .undone 日志；兼容旧3列格式（无备份列）。"""
    dubmap = {}
    try:
        logs = sorted(glob.glob(os.path.join(output_root, "distribute_log_dub_*.csv")))
    except Exception:
        return dubmap
    for logp in logs:
        if logp.endswith(".undone"):
            continue
        try:
            with open(logp, "r", encoding="utf-8-sig") as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    if len(row) < 2:
                        continue
                    orig, cur = row[0], row[1]
                    if not orig or not cur or cur.startswith(_BAD_ROW_PREFIX):
                        continue
                    bak = None
                    if len(row) >= 4 and row[3] and not row[3].startswith(_BAD_ROW_PREFIX):
                        bak = row[3]
                    dubmap[_pk(orig)] = (cur, bak, logp)
        except Exception:
            continue
    return dubmap


def load_rename_map(output_root):
    """读所有重命名日志，返回 {改名前路径: (改名后路径, 日志路径)}。"""
    rmap = {}
    try:
        logs = sorted(glob.glob(os.path.join(output_root, "distribute_log_rename_*.csv")))
    except Exception:
        return rmap
    for logp in logs:
        if logp.endswith(".undone"):
            continue
        try:
            with open(logp, "r", encoding="utf-8-sig") as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    if len(row) < 2:
                        continue
                    orig, cur = row[0], row[1]
                    if not orig or not cur or cur.startswith(_BAD_ROW_PREFIX):
                        continue
                    if "__HOLD__" in orig or "__hold_" in orig:
                        continue
                    rmap[_pk(orig)] = (cur, logp)
        except Exception:
            continue
    return rmap


def resolve_live_path(cur, dubmap=None, renamemap=None):
    """沿配乐/重命名链找到文件现在的位置。
    返回 (现路径或None, 是否原片内容, 是否经过配乐, 用到的日志路径set, 用备份恢复后需删的配乐成品或None)。
    有原片备份优先用备份（内容原样）；旧配乐无备份则退配乐后版本（位置和文件名恢复）。"""
    dubmap = dubmap or {}
    renamemap = renamemap or {}
    used = set()
    if cur and os.path.exists(cur):
        # 原地配乐（不勾“改名”）时日志里 orig==new，成品就还占着 cur，
        # 但内容已经是配乐版；这时必须优先用原片备份恢复，否则撤回把配乐版搬回源目录、
        # 原片被永久留在 .配乐原片备份_* 里。
        d = dubmap.get(_pk(cur))
        if d:
            dest, bak, lp = d
            used.add(lp)
            if bak and os.path.exists(bak):
                return bak, True, True, used, (cur if _pk(dest) != _pk(bak) and os.path.exists(cur) else None)
        return cur, True, False, used, None
    seen = {_pk(cur)}
    node = cur
    first_backup = None
    latest_dest = None
    passed_dub = False
    guard = 0
    while node and guard < 50:
        guard += 1
        nxt = None
        # 配乐边（含原片备份）
        d = dubmap.get(_pk(node))
        if d:
            dest, bak, lp = d
            used.add(lp)
            passed_dub = True
            if bak and first_backup is None and os.path.exists(bak):
                first_backup = bak
            if dest and os.path.exists(dest):
                latest_dest = dest  # 一路走到最新的存在位置
            if dest and _pk(dest) != _pk(node) and _pk(dest) not in seen:
                nxt = dest
        # 重命名边：原地配乐（dest==node）走不动时，靠这条边找到文件现在的位置
        r = renamemap.get(_pk(node))
        if r:
            dest, lp = r
            used.add(lp)
            if dest and os.path.exists(dest):
                latest_dest = dest
            if nxt is None and dest and _pk(dest) != _pk(node) and _pk(dest) not in seen:
                nxt = dest
        if nxt is None:
            break
        seen.add(nxt)
        node = nxt
    if first_backup:
        cleanup = latest_dest if (latest_dest and _pk(latest_dest) != _pk(first_backup)
                                  and os.path.exists(latest_dest)) else None
        return first_backup, True, passed_dub, used, cleanup
    if latest_dest:
        return latest_dest, (not passed_dub), passed_dub, used, None
    return None, False, passed_dub, used, None



def undo_distribute(move_log, output_root):
    """撤回分发：配乐/重命名过的文件沿日志链一起退回原位置+原文件名。
    有原片备份的恢复原片内容；旧配乐无备份的退配乐后版本（位置文件名恢复）。
    返回 (ok, fail, 配乐后版本数, 备份恢复数, 已标记.undone的日志list)。"""
    try:
        with open(move_log, "r", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        rows = rows[1:] if rows else []
    except Exception:
        return 0, 0, 0, 0, []
    try:
        dubmap = load_dub_maps(output_root)
        renamemap = load_rename_map(output_root)
    except Exception:
        dubmap, renamemap = {}, {}
    ok = fail = dubbed = from_backup = 0
    renamed_back = []
    retry_rows = []
    emptydirs = set()
    for row in reversed(rows):
        if len(row) < 2:
            continue
        orig, cur = row[0], row[1]
        if not orig or not cur or cur.startswith(_BAD_ROW_PREFIX):
            continue
        found, is_orig, passed_dub, used, cleanup = resolve_live_path(cur, dubmap, renamemap)
        if not found:
            # 链上找不到现文件（被手动删/移走），无法重试，不留待重试行
            fail += 1
            continue
        try:
            parent = os.path.dirname(orig)
            if parent:
                os.makedirs(parent, exist_ok=True)
            dest = orig
            if os.path.exists(dest):
                # 源位置又占用了同名文件：只能改名退回，并记下来告诉用户
                dest = unique_dest(dest)
                renamed_back.append(os.path.basename(dest))
            shutil.move(found, dest)
            ok += 1
            if passed_dub:
                if is_orig:
                    from_backup += 1
                else:
                    dubbed += 1
            # 用备份恢复后，配乐成品已多余，删掉（删失败就留着，不算错）
            if cleanup:
                try:
                    if os.path.exists(cleanup):
                        os.remove(cleanup)
                except Exception:
                    pass
                emptydirs.add(os.path.dirname(cleanup))
            # 现文件父目录可能空了，记下来稍后清理
            emptydirs.add(os.path.dirname(found))
        except Exception:
            fail += 1
            # 搬不动的（被占用/权限）必须留一条待重试行，否则这次失败就再也撤不回了
            retry_rows.append((orig, cur, row[2] if len(row) > 2 else ""))
    # 只清理空的日期文件夹和配乐备份目录
    for d in list(emptydirs):
        try:
            if d and os.path.isdir(d) and not os.listdir(d):
                base = os.path.basename(d)
                if "未分发" in base or base.startswith(DUB_BACKUP_PREFIX):
                    os.rmdir(d)
        except Exception:
            pass
    try:
        for name in os.listdir(output_root):
            if name.startswith(DUB_BACKUP_PREFIX):
                p = os.path.join(output_root, name)
                try:
                    if os.path.isdir(p) and not os.listdir(p):
                        os.rmdir(p)
                except Exception:
                    pass
    except Exception:
        pass
    marked = []
    retry_log = None
    # 只标记分发移动日志：配乐/重命名日志是多批分发共用的，
    # 整份标记会导致同日志里别批文件的撤回链断掉（部分撤回毒化整份日志）。
    # 过期的配乐/重命名行靠文件不存在自然失效，不影响别的批次。
    # 有搬不动的：把剩余行另存成新日志（撤回链还能接上），原日志才标记 .undone；
    # 连新日志都写不出来时，宁可保留原日志让它还能被再次尝试。
    keep_original = False
    if retry_rows:
        try:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            retry_log = unique_dest(os.path.join(output_root, f"distribute_log_{ts}.csv"))
            with open(retry_log, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["原路径", "新路径", "日期文件夹"])
                w.writerows(retry_rows)
        except Exception:
            retry_log = None
            keep_original = True
    if not keep_original:
        try:
            if not move_log.endswith(".undone") and os.path.exists(move_log):
                os.replace(move_log, move_log + ".undone")
                marked.append(move_log)
        except Exception:
            pass
    return ok, fail, dubbed, from_backup, marked, retry_log, renamed_back


def rename_existing(output_root, pattern="未分发", allowed_exts=None, rename_word="文件", progress=None):
    allowed = {e.lower() for e in (allowed_exts or ALL_EXTS)}
    renamed = 0
    rows = []
    try:
        names = sorted([d for d in os.listdir(output_root)
                        if os.path.isdir(os.path.join(output_root, d)) and pattern in d])
    except Exception:
        return 0, []
    for fname in names:
        # 配乐备份目录（.配乐原片备份_*）不含“未分发”一般进不来，双保险再挡一次
        if fname.startswith(DUB_BACKUP_PREFIX) or fname.startswith("."):
            continue
        d = os.path.join(output_root, fname)
        try:
            files = sorted([os.path.join(d, x) for x in os.listdir(d)
                            if os.path.isfile(os.path.join(d, x))
                            and not is_temp_media_name(x)
                            and os.path.splitext(x)[1].lower() in allowed],
                           key=lambda p: natsort_key(os.path.basename(p)))
        except Exception:
            continue
        # 第一遍：算出终名，跳过已正确的
        plan = []
        for i, src in enumerate(files, 1):
            if progress:
                try:
                    progress(i)
                except Exception:
                    pass
            ext = os.path.splitext(src)[1]
            # 按当前文件类型决定叫视频还是图片（混排时更准）
            w = rename_word
            if rename_word == "auto":
                w = "视频" if os.path.splitext(src)[1].lower() in VIDEO_EXTS else "图片"
            dest = os.path.join(d, f"{fname}{w}{i}{ext}")
            try:
                if os.path.abspath(src) == os.path.abspath(dest):
                    continue
            except Exception:
                pass
            plan.append((src, dest))
        if not plan:
            continue
        # 两阶段改名防碰撞：先全部改成 .__hold_N 临时名，再转正为终名
        # 失败回滚：第二阶段若有失败，把已转正的和还停在 hold 的都尽量恢复原名，
        # 避免留下 .__hold_ 半成品导致下次扫描误判
        holds = []
        for idx, (src, dest) in enumerate(plan):
            try:
                tmp_hold = os.path.join(d, f".__hold_{idx}_{os.path.basename(src)}")
                if os.path.exists(tmp_hold):
                    tmp_hold = unique_dest(tmp_hold)
                os.rename(src, tmp_hold)
                holds.append((src, tmp_hold, dest))
            except Exception as e:
                rows.append((src, f"FAILED:{e}", fname))
        # 第一阶段就有失败：把已进 hold 的先恢复原名再跳过本目录
        first_fail = len(holds) != len(plan)
        if first_fail:
            for src, tmp_hold, _dest in holds:
                try:
                    if os.path.exists(tmp_hold) and not os.path.exists(src):
                        os.rename(tmp_hold, src)
                except Exception:
                    rows.append((src, f"FAILED:回滚hold失败:{tmp_hold}", fname))
            continue
        second_fail = False
        for src, tmp_hold, dest in holds:
            try:
                final = dest
                if os.path.exists(final):
                    final = unique_dest(final)
                os.rename(tmp_hold, final)
                renamed += 1
                rows.append((src, final, fname))
            except Exception as e:
                second_fail = True
                rows.append((src, f"FAILED:{e}", fname))
        if second_fail:
            # 尽力把还停在 hold 的文件恢复原名（已转正的不动，已记日志可追溯）
            for src, tmp_hold, _dest in holds:
                try:
                    if os.path.exists(tmp_hold) and not os.path.exists(src):
                        os.rename(tmp_hold, src)
                except Exception as e2:
                    # 回不来的话文件就停在 .__hold_ 名字上，扫描全部会跳过它，
                    # 必须把现名写进日志，否则用户根本找不到这个文件
                    rows.append((src, f"FAILED:回滚失败,文件现名={os.path.basename(tmp_hold)}:{e2}", fname))
    return renamed, rows


def rename_with_library(folders, names, allowed_exts=None, progress=None):
    """按命名库给指定文件夹里的文件原地改名（随机取、批内不重复，用完才复用）。
    和“重命名已分发”一样两阶段：先全改成 .__hold_ 临时名再转正，防同批互撞。
    返回 (renamed, rows)，rows 与分发日志同列，写进 distribute_log_rename_*.csv 后，
    主界面“撤回上次”能沿重命名链把文件退回原名。"""
    allowed = {e.lower() for e in (allowed_exts or ALL_EXTS)}
    files = []
    for d in folders:
        try:
            d = os.path.abspath(str(d))
        except Exception:
            continue
        if not d or not os.path.isdir(d):
            continue
        try:
            # 补一个兜底 key：自然排序键相同时（例如只差大小写）保证顺序稳定，
            # 否则同一批文件每次分到的命名库名字可能不一样
            fns = sorted(os.listdir(d), key=lambda n: (natsort_key(n), n.lower(), n))
        except Exception:
            continue
        for fn in fns:
            if is_temp_media_name(fn):
                continue
            p = os.path.join(d, fn)
            if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in allowed:
                files.append(p)
    if not files or not names:
        return 0, []
    name_pool = names[:]
    random.shuffle(name_pool)
    used_names = set()
    groups = {}
    for f in files:
        groups.setdefault(os.path.dirname(f), []).append(f)
    rows = []
    renamed = 0
    done = 0
    for d, flist in groups.items():
        fname = os.path.basename(d)
        used_dests = set()
        plan = []
        for src in flist:
            done += 1
            if progress:
                try:
                    progress(done)
                except Exception:
                    pass
            ext = os.path.splitext(src)[1]
            avail = [n for n in name_pool if n not in used_names] or name_pool
            if not avail:
                break
            nm = random.choice(avail)
            used_names.add(nm)
            dest = unique_dest_seen(os.path.join(d, safe_filename(nm) + ext), used_dests)
            used_dests.add(os.path.normcase(dest))
            plan.append((src, dest))
        # 第一阶段：全部转 .__hold_ 临时名
        holds = []
        first_fail = False
        for idx, (src, dest) in enumerate(plan):
            tmp = os.path.join(d, f".__hold_{idx}_{os.path.basename(src)}")
            if os.path.exists(tmp):
                tmp = unique_dest(tmp)
            try:
                os.rename(src, tmp)
                holds.append((src, tmp, dest))
            except Exception as e:
                first_fail = True
                rows.append((src, f"FAILED:{e}", fname))
        if first_fail:
            # 有失败就把已进 hold 的恢复原名，跳过这个文件夹，别留半成品
            for src, tmp, _dest in holds:
                try:
                    if not os.path.exists(src) and os.path.exists(tmp):
                        os.rename(tmp, src)
                except Exception:
                    rows.append((src, f"FAILED:回滚hold失败:{tmp}", fname))
            continue
        # 第二阶段：转正为命名库终名
        for src, tmp, dest in holds:
            try:
                final = dest
                if os.path.exists(final):
                    final = unique_dest(dest)
                os.rename(tmp, final)
                renamed += 1
                rows.append((src, final, fname))
            except Exception as e:
                rows.append((src, f"FAILED:{e}", fname))
                try:
                    if not os.path.exists(src) and os.path.exists(tmp):
                        os.rename(tmp, src)
                except Exception as e2:
                    # 回滚也失败 = 文件卡在 .__hold_ 临时名上，所有扫描都会跳过它，
                    # 用户以为文件还在原名，其实谁都看不见。必须把现名写进日志。
                    rows.append((src, f"FAILED:回滚失败,文件现名={os.path.basename(tmp)}:{e2}", fname))
    return renamed, rows


def latest_log(output_root):
    # 只返回纯分发日志（过滤 _dub_ / _rename_），避免撤回/调试时拿到配乐日志
    try:
        cands = sorted(glob.glob(os.path.join(output_root, "distribute_log_*.csv")))
    except Exception:
        return None
    move_cands = [c for c in cands
                  if not c.endswith(".undone")
                  and "_dub_" not in os.path.basename(c)
                  and "_rename_" not in os.path.basename(c)]
    return move_cands[-1] if move_cands else None


def latest_restorable_log(output_root):
    """找最近一个还有可撤回文件的分发日志。
    只认 distribute_log_YYYYMMDD_HHMMSS.csv（分发移动日志）。
    配乐/重命名过的文件沿日志链追踪：只要原片备份或现文件还在，就算可撤回，
    撤回时连同配乐一起退。返回 (log_path, restorable_count)。"""
    cands = sorted(glob.glob(os.path.join(output_root, "distribute_log_*.csv")), reverse=True)
    # 先过滤出纯分发日志
    move_cands = []
    for c in cands:
        if c.endswith(".undone"):
            continue
        base = os.path.basename(c)
        if "_dub_" in base or "_rename_" in base:
            continue
        move_cands.append(c)
    if not move_cands:
        return None, 0
    try:
        dubmap = load_dub_maps(output_root)
        renamemap = load_rename_map(output_root)
    except Exception:
        dubmap, renamemap = {}, {}
    for c in move_cands:
        try:
            with open(c, "r", encoding="utf-8-sig") as f:
                r = csv.reader(f)
                next(r, None)
                n = 0
                for row in r:
                    if len(row) < 2 or not row[1]:
                        continue
                    if row[1].startswith("FAILED") or row[1].startswith("SKIPPED"):
                        continue
                    # 配乐/重命名过的文件沿日志链找现位置，找得到就算可撤回
                    found, _iso, _pd, _u, _c = resolve_live_path(row[1], dubmap, renamemap)
                    if found:
                        n += 1
            if n > 0:
                return c, n
        except Exception:
            continue
    return move_cands[0], 0


def parse_start_date(s, default=None):
    s = (s or "").strip()
    if not s:
        return default or datetime.date.today()
    try:
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"(\d{1,2})-(\d{1,2})", s)
        if m:
            y = (default or datetime.date.today()).year
            return datetime.date(y, int(m.group(1)), int(m.group(2)))
        m = re.match(r"(\d{1,2})月(\d{1,2})", s)
        if m:
            y = (default or datetime.date.today()).year
            return datetime.date(y, int(m.group(1)), int(m.group(2)))
    except Exception:
        pass
    return default or datetime.date.today()


def is_valid_date_str(s):
    """严格校验：空算有效（用今天），否则必须是 YYYY-MM-DD / MM-DD / M月D日 且合法日期。
    MM-DD 按当年年份校验（02-29 非闰年算无效，避免校验通过但解析回退到今天的坑）,"""
    s = (s or "").strip()
    if not s:
        return True
    try:
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
        if m:
            datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return True
        y = datetime.date.today().year
        m = re.match(r"^(\d{1,2})-(\d{1,2})$", s)
        if m:
            datetime.date(y, int(m.group(1)), int(m.group(2)))
            return True
        m = re.match(r"^(\d{1,2})月(\d{1,2})日?$", s)
        if m:
            datetime.date(y, int(m.group(1)), int(m.group(2)))
            return True
    except Exception:
        return False
    return False


# ---------------- 配乐（ffmpeg + 命名库改名，无 LLM） ----------------
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".aac", ".wma"}


def _no_window():
    try:
        return subprocess.CREATE_NO_WINDOW
    except Exception:
        return 0


def ffmpeg_path():
    cands = []
    # PyInstaller单文件包：二进制解压到 _MEIPASS
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(os.path.join(meipass, "ffmpeg.exe"))
    except Exception:
        pass
    cands += [os.path.join(get_app_dir(), "ffmpeg.exe")]
    try:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe"))
    except Exception:
        pass
    for c in cands:
        try:
            if c and os.path.exists(c):
                return c
        except Exception:
            pass
    for c in ["ffmpeg"]:
        try:
            if shutil.which(c):
                return c
        except Exception:
            pass
    return "ffmpeg"


def ffmpeg_available(ff=None):
    """给状态栏用： bundled 路径存在 或 PATH 里能找到 即为可用。"""
    ff = ff or ffmpeg_path()
    try:
        if ff and ff != "ffmpeg" and os.path.exists(ff):
            return True
    except Exception:
        pass
    try:
        return shutil.which("ffmpeg") is not None or shutil.which(ff) is not None
    except Exception:
        return False


def ffprobe_path(ff=None):
    """ffprobe 与 ffmpeg 同目录优先；没有则用 PATH。返回 None 表示不可用。"""
    cands = []
    try:
        base = None
        if ff and ff != "ffmpeg" and os.path.isfile(ff):
            base = os.path.dirname(os.path.abspath(ff))
        if base:
            for name in ("ffprobe.exe", "ffprobe"):
                cands.append(os.path.join(base, name))
        appd = get_app_dir()
        cands.append(os.path.join(appd, "ffprobe.exe"))
    except Exception:
        pass
    for c in cands:
        try:
            if c and os.path.exists(c):
                return c
        except Exception:
            continue
    try:
        w = shutil.which("ffprobe")
        if w:
            return w
    except Exception:
        pass
    return None


_probe_cache = {}


def _probe_cache_key(path):
    try:
        st = os.stat(path)
        return (os.path.abspath(path), st.st_size, int(st.st_mtime))
    except Exception:
        return (os.path.abspath(path), -1, -1)


def ff_probe(path, ff=None):
    """一次进程同时拿 (时长秒, 是否含音频)。优先 ffprobe(JSON)，失败回退 ffmpeg -i。
    带按文件mtime+size的内存缓存：同一文件重复问只跑一次子进程。"""
    key = _probe_cache_key(path)
    hit = _probe_cache.get(key)
    if hit is not None:
        return hit
    dur, has_audio = 0, False
    # 1) ffprobe：快且输出稳定
    fp = ffprobe_path(ff if ff else None)
    if fp:
        try:
            p = subprocess.run([fp, "-v", "error",
                                "-show_entries", "format=duration:stream=codec_type",
                                "-of", "json", path],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=15, creationflags=_no_window())
            obj = json.loads(p.stdout or "{}")
            try:
                dur = float((obj.get("format") or {}).get("duration") or 0)
            except Exception:
                dur = 0
            try:
                for s in (obj.get("streams") or []):
                    if (s.get("codec_type") or "").lower() == "audio":
                        has_audio = True
                        break
            except Exception:
                pass
            if dur > 0:
                _probe_cache[key] = (dur, has_audio)
                # 缓存别无限涨：超 2000 条清一半
                if len(_probe_cache) > 2000:
                    for k in list(_probe_cache)[:1000]:
                        _probe_cache.pop(k, None)
                return dur, has_audio
        except Exception:
            pass
    # 2) 回退：一次 ffmpeg -i 同时解析时长+音频（原来要跑 2-3 次）
    ff = ff or ffmpeg_path()
    try:
        p = subprocess.run([ff, "-i", path], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15, creationflags=_no_window())
        out = (p.stderr or "") + (p.stdout or "")
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out)
        if m:
            dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        has_audio = "Audio:" in out
    except Exception:
        pass
    _probe_cache[key] = (dur, has_audio)
    if len(_probe_cache) > 2000:
        for k in list(_probe_cache)[:1000]:
            _probe_cache.pop(k, None)
    return dur, has_audio


def list_audios(music_dir):
    if not music_dir or not os.path.isdir(music_dir):
        return []
    out = []
    try:
        names = os.listdir(music_dir)
    except Exception:
        # 盘符掉线/权限问题：返回空列表由调用方提示，不能把异常抛到 Tk 回调里
        return []
    for fn in names:
        if fn.startswith("."):
            continue
        if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
            out.append(os.path.join(music_dir, fn))
    return sort_files(out)


def dubbed_live_set(output_root):
    """当前仍算“已配过乐”的现存路径集合（normcase）。
    配乐后又被“重命名已分发”改了名的文件，路径变了但内容仍是配乐版，
    必须沿重命名链追踪，否则下次配乐会重复配（二次配乐+二次改名）。
    实现：dub 产物的闭包 + 重命名正向映射反复推导，直到不动点。"""
    try:
        dubmap = load_dub_maps(output_root)
        renamemap = load_rename_map(output_root)
    except Exception:
        return set()
    try:
        closed = set()
        for _dest, _bak, _lp in dubmap.values():
            try:
                if _dest:
                    closed.add(os.path.normcase(os.path.normpath(_dest)))
            except Exception:
                continue
        if not closed:
            return set()
        try:
            fwd = {}
            for _o, (_d, _lp) in renamemap.items():
                try:
                    fwd[os.path.normcase(os.path.normpath(_o))] = os.path.normcase(os.path.normpath(_d))
                except Exception:
                    continue
        except Exception:
            return closed
        # 不动点迭代（链一般只有1~2跳，50轮封顶防环）
        for _ in range(50):
            grew = False
            for _o, _d in list(fwd.items()):
                if _o in closed and _d not in closed:
                    closed.add(_d)
                    grew = True
            if not grew:
                break
        return closed
    except Exception:
        return set()


def autofix_music_dir(saved):
    """音乐库自动纠正：保存的路径不存在时，优先用工具目录下的 音乐库，不弹窗。
    支持相对路径（相对于程序目录）。"""
    try:
        appd = get_app_dir()
        # 先把相对路径解析为绝对路径
        if saved and not os.path.isabs(saved):
            saved = os.path.normpath(os.path.join(appd, saved))
        if saved and os.path.isdir(saved):
            return saved
    except Exception:
        pass
    try:
        appd = get_app_dir()
        for cand in [os.path.join(appd, "音乐库"),
                     os.path.join(appd, "外部依赖", "音乐库")]:
            if os.path.isdir(cand):
                return cand
    except Exception:
        pass
    return saved or ""


def autofix_name_dir(saved):
    """命名库自动纠正：保存的路径不存在时，优先用工具目录下的 命名库，不弹窗。
    支持相对路径（相对于程序目录）。"""
    try:
        appd = get_app_dir()
        if saved and not os.path.isabs(saved):
            saved = os.path.normpath(os.path.join(appd, saved))
        if saved and os.path.isdir(saved):
            return saved
    except Exception:
        pass
    try:
        appd = get_app_dir()
        for cand in [os.path.join(appd, "命名库"),
                     os.path.join(appd, "外部依赖", "命名库")]:
            if os.path.isdir(cand):
                return cand
    except Exception:
        pass
    return saved or ""


def load_names(name_dir):
    """读取命名库：每行一个命名，去空行去重，返回列表。

    name_dir 可以是：
      · 文件夹 → 读该文件夹下所有 TXT（老配置一直是文件夹，保持不变）
      · 单个 .txt 文件 → 只读它（现在"选命名库"可以直接挑 TXT 文件）
    编码按 utf-8-sig / utf-8 / gbk 依次试，Windows 记事本存的 ANSI 也能读。
    """
    if not name_dir:
        return []
    name_dir = str(name_dir)
    if os.path.isfile(name_dir):
        if not name_dir.lower().endswith(".txt"):
            return []
        files = [name_dir]
    elif os.path.isdir(name_dir):
        try:
            files = [os.path.join(name_dir, fn) for fn in sorted(os.listdir(name_dir), key=natsort_key)
                     if not fn.startswith(".") and fn.lower().endswith(".txt")
                     and os.path.isfile(os.path.join(name_dir, fn))]
        except Exception:
            return []
    else:
        return []
    names = []
    seen = set()
    for path in files:
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                with open(path, "r", encoding=enc) as f:
                    text = f.read()
                break
            except Exception:
                continue
        if text is None:
            continue
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            name = line.strip()
            if not name:
                continue
            key = name.lower()
            if key not in seen:
                seen.add(key)
                names.append(name)
    return names


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def is_temp_media_name(basename):
    """输出目录里的临时/半成品：崩溃残留的 .__dub_* / .__hold_*、点开头隐藏文件。
    重命名和配乐扫描必须跳过它们，否则会把临时文件当正片改名/配乐。"""
    try:
        b = (basename or "").strip()
    except Exception:
        return True
    if not b:
        return True
    if b.startswith("."):
        return True
    low = b.lower()
    if low.startswith("__hold_") or low.startswith("__dub_") or low.startswith(".__"):
        return True
    return False


def safe_filename(s, maxlen=80):
    s = re.sub(r'[\\/:*?"<>|]', "", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    # Windows 不允许以空格/点结尾，去掉；顺手去尾部空格点
    s = s.strip().rstrip(" .")
    s = s[:maxlen].rstrip(" .") if s else "Untitled"
    # 控制单段文件名长度，避免加上扩展名/文件夹前缀后超 Windows 260 限制
    # （NTFS 单段 255，这里留余量截到 120）
    if len(s) > 120:
        s = s[:120].rstrip(" .") or "Untitled"
    # Windows 保留名（CON/PRN/AUX/NUL/COM1../LPT1..，不区分大小写、带扩展名也算）
    # LLM 标题极小概率以这类词开头，提前加下划线，否则创建文件直接报错
    try:
        stem = s.split(".")[0].strip().upper()
        if stem in _WIN_RESERVED:
            s = "_" + s
    except Exception:
        pass
    return s or "Untitled"


DUB_WORKERS = 4  # 配乐转码并发：-c:v copy 视频流直拷+音频轻编码，I/O为主，4路在多核+SSD下CPU占用依然很低


def audio_args_for(out_ext):
    """按输出容器选音频编码：一刀切 aac 在 avi/wmv/mpg 里兼容差或体积异常，
    webm 则根本不支持 aac。返回 ffmpeg 的 ['-c:a', codec, '-b:a', '128k']。”
    """
    e = (out_ext or "").lower()
    if e == ".webm":
        return ["-c:a", "libopus", "-b:a", "128k"]
    if e == ".avi":
        return ["-c:a", "libmp3lame", "-b:a", "128k"]
    if e in (".wmv", ".asf"):
        return ["-c:a", "wmav2", "-b:a", "128k"]
    if e in (".mpg", ".mpeg"):
        return ["-c:a", "mp2", "-b:a", "128k"]
    return ["-c:a", "aac", "-b:a", "128k"]


def dub_one_video(video_path, music_path, start_offset, remove_original, ff=None):
    """ffmpeg换音频：去掉原声或混音，音频截取/循环到视频等长。
    返回 (tmp_path_or_None, err_msg)。
    注意：tmp 建在视频同目录，避免 C盘temp -> D盘目标 跨盘 os.replace 报 WinError 17。"""
    ff = ff or ffmpeg_path()
    if not ff or ff == "ffmpeg":
        if not shutil.which("ffmpeg"):
            return None, f"找不到ffmpeg（path={ff}），请把ffmpeg.exe和本工具放同一目录"
    # 一次 probe 同时拿视频时长+有无音频（原来要跑 2-3 次子进程）；音乐时长走缓存
    dur, has_audio = ff_probe(video_path, ff)
    if dur <= 0:
        return None, f"读不到视频时长：{os.path.basename(video_path)}"
    # 起始偏移钳制到音频时长内，避免-ss越界导致空输出
    try:
        adur, _ = ff_probe(music_path, ff)
    except Exception:
        adur = 0
    try:
        start = max(0, float(start_offset or 0))
    except Exception:
        start = 0
    if adur > 1:
        start = min(start, max(0, adur - 1))
    # has_audio 已由上面的 ff_probe 一次拿到；混音需要原视频有音频流，否则退化为替换
    # 同盘临时文件：解决跨盘 os.replace/shutil.move 失败 + 避免 mktemp 安全问题
    tmp = None
    try:
        out_dir = os.path.dirname(os.path.abspath(video_path)) or get_app_dir()
        fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(video_path)[1], prefix=".__dub_",
                                   dir=out_dir)
        os.close(fd)
    except Exception:
        # 回退到旧行为（系统 temp），调用方会用 shutil.move 尽力搬运
        try:
            fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(video_path)[1])
            os.close(fd)
        except Exception as e:
            return None, f"建临时文件失败：{e}"
    # 音频编码按容器选（webm/opus、avi/mp3、wmv/wma、mpg/mp2，其余 aac）
    out_ext = os.path.splitext(video_path)[1].lower()
    audio_args = audio_args_for(out_ext)
    if remove_original or not has_audio:
        # -stream_loop 循环短音频，-t 裁到视频时长，视频流直接拷贝
        cmd = [ff, "-y", "-i", video_path,
               "-stream_loop", "-1", "-ss", str(start), "-i", music_path,
               "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "copy"] + audio_args + [
               "-t", str(dur), "-shortest", tmp]
    else:
        cmd = [ff, "-y", "-i", video_path,
               "-stream_loop", "-1", "-ss", str(start), "-i", music_path,
               "-filter_complex", "[0:a]volume=0.4[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first[a]",
               "-map", "0:v:0", "-map", "[a]",
               "-c:v", "copy"] + audio_args + [
               "-t", str(dur), "-shortest", tmp]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, creationflags=_no_window())
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            return tmp, ""
        err = ((p.stderr or "") + (p.stdout or "")).strip().splitlines()
        tail = "\n".join(err[-6:]) if err else "ffmpeg无输出"
        try:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None, f"ffmpeg转码失败：{tail[:800]}"
    except Exception as e:
        try:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None, f"转码异常：{e}"

def big_button(btn, accent=SEA_DEEP, primary=True):
    """中等偏大的动作按钮（比 neon_button 明显好按，但不至于占满一行）。

    字号和内边距两档必须一致，否则两个按钮并排会一高一低，看着像没对齐。
    """
    common = dict(relief="groove", cursor="hand2",
                  font=("Microsoft YaHei UI", 10, "bold"), padx=14, pady=6)
    if primary:
        btn.configure(bg=accent, fg="#FFFFFF", activebackground=CORAL,
                      activeforeground="#FFFFFF", **common)
    else:
        btn.configure(bg="#FFFFFF", fg=accent, activebackground="#DFF3F3",
                      activeforeground=SEA_DEEP, **common)
    return btn


def neon_button(btn, accent=SEA_DEEP):
    # 小清新：白底、海蓝色文字描边，悬停变浅蓝
    btn.configure(bg="#FFFFFF", fg=accent, activebackground="#DFF3F3", activeforeground=SEA_DEEP,
                  relief="groove", bd=1, highlightbackground="#BFE6EC", highlightthickness=1,
                  font=FONT_BOLD, cursor="hand2", padx=10, pady=4)
    return btn


def style_root(root):
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=TEXT,
                    rowheight=24, font=FONT_MAIN, borderwidth=0)
    style.configure("Treeview.Heading", background="#DFF3F3", foreground=SEA_DEEP, font=FONT_BOLD)
    style.map("Treeview", background=[("selected", "#BFE6EC")], foreground=[("selected", SEA_DEEP)])
    return style


class WizardDialog(tk.Toplevel):
    def __init__(self, master, init_date, init_per, init_interval):
        super().__init__(master)
        self.title("🌊 分发设置 · 小清新")
        self.configure(bg=SAND)
        self.resizable(False, False)
        self.result = None
        tk.Label(self, text="🌊 从几号开始？(如 2026-09-08，默认今天)", bg=SAND, fg=SEA_DEEP, font=FONT_BOLD).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        self.e_date = tk.Entry(self, width=22, bg="#FFFFFF", fg=TEXT, insertbackground=SEA_DEEP)
        self.e_date.insert(0, init_date.strftime("%Y-%m-%d"))
        self.e_date.grid(row=0, column=1, padx=10, pady=5)
        tk.Label(self, text="🐚 每个文件夹放几个文件？", bg=SAND, fg=SEA_DEEP, font=FONT_BOLD).grid(row=1, column=0, sticky="w", padx=10, pady=5)
        self.e_per = tk.Entry(self, width=22, bg="#FFFFFF", fg=TEXT, insertbackground=SEA_DEEP)
        self.e_per.insert(0, str(init_per))
        self.e_per.grid(row=1, column=1, padx=10, pady=5)
        tk.Label(self, text="⛵ 日期间隔几天？(0=连续,1=隔1天如8号→10号)", bg=SAND, fg=SEA_DEEP, font=FONT_BOLD).grid(row=2, column=0, sticky="w", padx=10, pady=5)
        self.e_interval = tk.Entry(self, width=22, bg="#FFFFFF", fg=TEXT, insertbackground=SEA_DEEP)
        self.e_interval.insert(0, str(init_interval))
        self.e_interval.grid(row=2, column=1, padx=10, pady=5)
        frm = tk.Frame(self, bg=SAND)
        frm.grid(row=3, column=0, columnspan=2, pady=10)
        b1 = tk.Button(frm, text="⛱ 确定", width=12, command=self.on_ok)
        b2 = tk.Button(frm, text="跳过", width=12, command=self.on_skip)
        neon_button(b1, SEA_DEEP).pack(side="left", padx=10)
        neon_button(b2, CORAL).pack(side="left", padx=10)
        self.grab_set()
        self.e_date.focus_set()

    def on_ok(self):
        try:
            if not is_valid_date_str(self.e_date.get()):
                messagebox.showwarning("提示", "日期格式不对，用 2026-09-08 或 09-08 或 9月8日")
                return
            d = parse_start_date(self.e_date.get())
            per = max(1, int(self.e_per.get()))
            inv = max(0, min(MAX_INTERVAL_DAYS, int(self.e_interval.get())))
        except Exception:
            messagebox.showwarning("提示", "数字格式不对，请检查")
            return
        self.result = (d, per, inv)
        self.destroy()

    def on_skip(self):
        self.destroy()


class App:
    def __init__(self, root):
        self.root = root
        style_root(root)
        root.title(f"🌊 视频/图片分发工具 · 小清新 {APP_VERSION} · 构建{build_stamp()} 🌊")
        root.geometry("960x800")
        try:
            # 下限宽度 = 最宽的一行控件宽度（分发规则行 ~763px）+ 余量，卡在 800。
            # 高度下限不能瞎猜：两页里"按日期分发"页的内容最满，若窗口比它还矮，
            # 页面会溢出并盖住下面的音乐库栏（按钮看不见）。
            # 这里在两页都建好之后按真实请求高度反推，见 _apply_min_size()。
            root.minsize(800, 560)
        except Exception:
            pass
        cfg = load_config()
        # 配置容错：手改/损坏的 config 不应导致启动崩溃
        try:
            _per = int(cfg.get("per_group", 15))
        except Exception:
            _per = 15
        try:
            _inv = int(cfg.get("interval", 0))
        except Exception:
            _inv = 0
        _per = max(1, _per)
        _inv = max(0, _inv)
        self.output_root = tk.StringVar(value=cfg.get("output_root", ""))
        # 每次打开都用当天日期，不读旧配置里的起始日期
        self.start_date = datetime.date.today()
        self.per_group = tk.IntVar(value=_per)
        self.interval = tk.IntVar(value=_inv)
        self.do_rename = tk.BooleanVar(value=bool(cfg.get("rename", True)))
        self.media_type = tk.StringVar(value=cfg.get("media_type", "all"))
        self.shuffle = tk.BooleanVar(value=bool(cfg.get("shuffle", False)))
        try:
            _md = autofix_music_dir(cfg.get("music_dir", ""))
        except Exception:
            _md = cfg.get("music_dir", "")
        if not _md:
            # 默认指向程序目录下的 音乐库（便携），不再硬编码 D 盘
            try:
                _md = os.path.join(get_app_dir(), "音乐库")
            except Exception:
                _md = ""
        self.music_dir = tk.StringVar(value=_md)
        self.remove_original = tk.BooleanVar(value=bool(cfg.get("remove_original", True)))
        # 一键配乐默认只换音乐，不改名不改标题；勾选“改名”才用命名库随机改名
        self.dub_rename = tk.BooleanVar(value=bool(cfg.get("dub_rename", False)))
        try:
            _nd = autofix_name_dir(cfg.get("name_dir", ""))
        except Exception:
            _nd = cfg.get("name_dir", "")
        if not _nd:
            try:
                _nd = os.path.join(get_app_dir(), "命名库")
            except Exception:
                _nd = ""
        self.name_dir = tk.StringVar(value=_nd)
        # 均分/自定义分发窗口（新界面）的设置，跟主界面一起持久化
        self.split_mode = tk.StringVar(value=("custom" if cfg.get("split_mode") == "custom" else "even"))
        try:
            _sn = int(cfg.get("split_n", 4))
        except Exception:
            _sn = 4
        self.split_n = tk.IntVar(value=max(1, min(100, _sn)))
        self.split_prefix = tk.StringVar(value=str(cfg.get("split_prefix", "素材") or "素材"))
        self.split_suffix = tk.BooleanVar(value=bool(cfg.get("split_suffix", True)))
        self.videos = []
        self.plan = []
        self._last_drop_ms = 0
        self._dubbing = False
        self._executing = False
        self._undoing = False
        self._rename_busy = False
        self._dub_cancel = threading.Event()

        # 根窗口用 grid 而不是 pack：pack 在空间不够时是从"最后排队的控件"开始裁，
        # 窗口一小就会把底部功能按钮/状态栏裁没。grid 给"可伸缩区"配权重，
        # 缩小时只压表格和日志，功能按钮行永远保留。
        for r, w in ((0, 0), (1, 0), (2, 0), (3, 1), (4, 0), (5, 0), (6, 0)):
            root.rowconfigure(r, weight=w, minsize=0)
        root.columnconfigure(0, weight=1)

        # 标题条（两个页面共用，永远在最上面）
        title = tk.Label(root, text="🌊 分发小站 · 视频 / 图片 ⛵",
                         bg=BG, fg=SEA_DEEP, font=FONT_TITLE)
        title.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        sub = tk.Label(root, text="🐚 完全本地运行 · 一个程序两个页面：按日期分发 / 均分多文件夹 🐚",
                       bg=BG, fg=MUTED, font=FONT_MAIN)
        sub.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 2))

        # 页面切换条
        nav = tk.Frame(root, bg=BG)
        nav.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.btn_page_date = neon_button(tk.Button(nav, text="📅 按日期分发",
                                                   command=lambda: self.show_page("date")), SEA_DEEP)
        self.btn_page_split = neon_button(tk.Button(nav, text="🧮 均分 / 自定义数量",
                                                    command=lambda: self.show_page("split")), SEA_DEEP)
        for b in (self.btn_page_date, self.btn_page_split):
            b.pack(side="left", padx=3)
        tk.Label(nav, text="切换页面：素材各自独立，音乐库/命名库两边共用", bg=BG, fg=MUTED,
                 font=FONT_MAIN).pack(side="left", padx=8)

        # 页面容器：只显示当前页，另一个 grid_remove（保留 grid 布局）
        self.pages = tk.Frame(root, bg=BG)
        self.pages.grid(row=3, column=0, sticky="nsew")
        self.pages.rowconfigure(0, weight=1)
        self.pages.columnconfigure(0, weight=1)
        self.page_date = tk.Frame(self.pages, bg=BG)
        self.page_split = tk.Frame(self.pages, bg=BG)
        # page_split 里还嵌了一层 SplitPage：不给这一行权重的话，
        # 内层页面会保持自己的请求高度（403px），窗口再高也不会撑开，
        # 底部就留出一条空白。page_date 直接就是内容页，不需要这层。
        self.page_split.rowconfigure(0, weight=1)
        self.page_split.columnconfigure(0, weight=1)
        # 记住上次停在哪一页
        self._page = "split" if cfg.get("page") == "split" else "date"
        if self._page == "split":
            self.page_split.grid(row=0, column=0, sticky="nsew")
        else:
            self.page_date.grid(row=0, column=0, sticky="nsew")
        self._style_page_btns()

        # 音乐库/命名库：两个页面共用，固定显示在页面区下面
        libcfg = tk.LabelFrame(root, text="🎵 音乐库 / 命名库 · 两个页面共用同一套 🎵")
        libcfg.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 4))
        libcfg.configure(bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        lib_top = tk.Frame(libcfg, bg=PANEL)
        lib_top.pack(fill="x")
        tk.Label(lib_top, text="音乐库：", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        tk.Entry(lib_top, textvariable=self.music_dir, width=28, bg="#FFFFFF", fg=TEXT).pack(side="left", padx=2)
        neon_button(tk.Button(lib_top, text="选音乐库", command=self.choose_music), SEA_BLUE).pack(side="left", padx=2)
        tk.Checkbutton(lib_top, text="去掉原声", variable=self.remove_original, bg=PANEL, fg=CORAL,
                       selectcolor="#FFFFFF", command=self.persist).pack(side="left", padx=6)
        tk.Checkbutton(lib_top, text="配乐时改名（用命名库）", variable=self.dub_rename, bg=PANEL, fg=SEA_DEEP,
                       selectcolor="#FFFFFF", command=self.persist).pack(side="left", padx=4)
        self.lbl_llm = tk.Label(lib_top, text="", bg=PANEL, fg=MUTED, font=FONT_MAIN)
        self.lbl_llm.pack(side="left", padx=6)
        lib_bot = tk.Frame(libcfg, bg=PANEL)
        lib_bot.pack(fill="x", pady=(2, 0))
        tk.Label(lib_bot, text="命名库：", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        tk.Entry(lib_bot, textvariable=self.name_dir, width=28, bg="#FFFFFF", fg=TEXT).pack(side="left", padx=2)
        # 选命名库 = 挑 TXT 文件（能按 *.txt 筛选）；选文件夹 = 整个目录一起读
        neon_button(tk.Button(lib_bot, text="选TXT", command=self.choose_name_dir), SEA_BLUE).pack(side="left", padx=2)
        neon_button(tk.Button(lib_bot, text="选文件夹", command=self.choose_name_folder), SEA_BLUE).pack(side="left", padx=2)
        tk.Label(lib_bot, text="TXT每行一个命名，改名时随机取（用完才复用）",
                 bg=PANEL, fg=MUTED, font=FONT_MAIN).pack(side="left", padx=4)
        # 状态栏先给个即时值，真实状态后台异步刷，避免启动卡3秒
        try:
            self.lbl_llm.config(text="正在检查…")
        except Exception:
            pass

        pd = self.page_date
        top = tk.Frame(pd, bg=BG)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        tk.Label(top, text="🏖 分发位置：", bg=BG, fg=SEA_DEEP, font=FONT_BOLD).pack(side="left")
        tk.Entry(top, textvariable=self.output_root, width=50, bg="#FFFFFF", fg=TEXT,
                 insertbackground=SEA_DEEP, relief="groove",
                 highlightbackground="#BFE6EC", highlightthickness=1).pack(side="left", padx=4)
        neon_button(tk.Button(top, text="浏览...", command=self.choose_output), SEA_DEEP).pack(side="left", padx=4)
        neon_button(tk.Button(top, text="打开看看", command=self.open_output), CORAL).pack(side="left")

        # 类型切换
        mf = tk.Frame(pd, bg=BG)
        mf.grid(row=1, column=0, sticky="ew", padx=8, pady=2)
        tk.Label(mf, text="🐠 分发类型：", bg=BG, fg=SEA_DEEP, font=FONT_BOLD).pack(side="left")
        for val, txt, color in [("video", "🎬 视频", SEA_DEEP), ("image", "📷 图片", CORAL), ("all", "🌊 视频+图片", SEA_BLUE)]:
            tk.Radiobutton(mf, text=txt, value=val, variable=self.media_type,
                           bg=BG, fg=color, selectcolor="#FFFFFF", activebackground=BG,
                           activeforeground=color, font=FONT_BOLD,
                           command=self.on_media_change).pack(side="left", padx=8)

        sf = tk.LabelFrame(pd, text="⛵ 分发规则 · 向导值可改，表格可逐行改 🐚")
        sf.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        sf.configure(bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        tk.Label(sf, text="从", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        # 起始日期用共享 StringVar：均分页显示/修改的是同一个值，两页永远一致
        self.start_date_var = tk.StringVar(value=self.start_date.strftime("%Y-%m-%d"))
        self.e_start = tk.Entry(sf, textvariable=self.start_date_var, width=13, bg="#FFFFFF", fg=TEXT,
                                insertbackground=SEA_DEEP)
        self.e_start.pack(side="left")
        tk.Label(sf, text="开始 每组", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        tk.Entry(sf, textvariable=self.per_group, width=6, bg="#FFFFFF", fg=TEXT, insertbackground=SEA_DEEP).pack(side="left")
        tk.Label(sf, text="个 间隔", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        tk.Entry(sf, textvariable=self.interval, width=6, bg="#FFFFFF", fg=TEXT, insertbackground=SEA_DEEP).pack(side="left")
        tk.Label(sf, text="天(0=连续)", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        neon_button(tk.Button(sf, text="自动生成计划", command=self.auto_plan), SEA_DEEP).pack(side="left", padx=8)
        neon_button(tk.Button(sf, text="重设向导", command=self.show_wizard), SEA_BLUE).pack(side="left")
        tk.Checkbutton(sf, text="改名", variable=self.do_rename,
                       bg=PANEL, fg=CORAL, selectcolor="#FFFFFF",
                       activebackground=PANEL, command=self.persist).pack(side="left", padx=8)
        tk.Checkbutton(sf, text="🌊 打乱分发", variable=self.shuffle,
                       bg=PANEL, fg=SEA_DEEP, selectcolor="#FFFFFF",
                       activebackground=PANEL, command=self.on_toggle_shuffle).pack(side="left", padx=4)
        neon_button(tk.Button(sf, text="换一批", command=self.on_reshuffle), CORAL).pack(side="left", padx=4)

        mid = tk.Frame(pd, bg=BG)
        mid.grid(row=3, column=0, sticky="nsew", padx=8, pady=4)
        # 表格区请求高度固定（靠权重拉大），窗口变矮时不会把底部按钮顶出去
        mid.configure(height=150)
        # 子控件用 pack 放的，必须 pack_propagate(False) 才能压住请求高度
        mid.pack_propagate(False)

        left = tk.LabelFrame(mid, text="🐚 待分发素材（拖文件/文件夹进来） 🌊", bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        btns = tk.Frame(left, bg=PANEL)
        btns.pack(fill="x")
        neon_button(tk.Button(btns, text="加文件", command=self.add_files), SEA_DEEP).pack(side="left", padx=2, pady=2)
        neon_button(tk.Button(btns, text="加文件夹", command=self.add_folder), SEA_DEEP).pack(side="left", padx=2)
        neon_button(tk.Button(btns, text="清空", command=self.clear_videos), CORAL).pack(side="left", padx=2)
        self.lbl_count = tk.Label(btns, text="共0个", bg=PANEL, fg=CORAL, font=FONT_BOLD)
        self.lbl_count.pack(side="right", padx=4)
        self.listbox = tk.Listbox(left, selectmode="extended", bg="#FFFFFF", fg=TEXT,
                                  selectbackground="#BFE6EC", selectforeground=SEA_DEEP)
        self.listbox.pack(fill="both", expand=True, side="left")
        sb1 = tk.Scrollbar(left, command=self.listbox.yview)
        sb1.pack(fill="y", side="right")
        self.listbox.config(yscrollcommand=sb1.set)

        right = tk.LabelFrame(mid, text="⛵ 分发计划表（双击可改） 🐚", bg=PANEL, fg=CORAL, font=FONT_BOLD)
        right.pack(side="left", fill="both", expand=True, padx=(4, 0))
        rbtn = tk.Frame(right, bg=PANEL)
        rbtn.pack(fill="x")
        neon_button(tk.Button(rbtn, text="+加一行", command=self.plan_add), SEA_DEEP).pack(side="left", padx=2, pady=2)
        neon_button(tk.Button(rbtn, text="-删选中", command=self.plan_del), CORAL).pack(side="left", padx=2)
        self.lbl_plan = tk.Label(rbtn, text="计划0个", bg=PANEL, fg=CORAL, font=FONT_BOLD)
        self.lbl_plan.pack(side="right", padx=4)
        cols = ("date", "count")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=10)
        self.tree.heading("date", text="日期 (MM-DD未分发)")
        self.tree.heading("count", text="数量")
        self.tree.column("date", width=150)
        self.tree.column("count", width=60)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.plan_edit)

        bot = tk.Frame(pd, bg=BG)
        bot.grid(row=4, column=0, sticky="ew", padx=8, pady=4)
        neon_button(tk.Button(bot, text="🌊 预览分配", command=self.preview), SEA_DEEP).pack(side="left", padx=4)
        self.b_exec = tk.Button(bot, text="⛵ 确认执行（移动）", command=self.execute)
        self.b_exec.configure(bg=CORAL, fg="#FFFFFF", activebackground=SEA_DEEP, activeforeground="#FFFFFF",
                              relief="groove", font=FONT_BOLD, cursor="hand2", padx=12, pady=4)
        self.b_exec.pack(side="left", padx=4)
        neon_button(tk.Button(bot, text="🐚 撤回上次", command=self.on_undo), SEA_BLUE).pack(side="left", padx=4)
        neon_button(tk.Button(bot, text="📷 重命名已分发", command=self.on_rename_existing), SEA_DEEP).pack(side="left", padx=4)
        self.b_split = tk.Button(bot, text="🧮 切到均分/自定义数量分发", command=self.open_split)
        self.b_split.configure(bg=SEA_BLUE, fg="#FFFFFF", activebackground=SEA_DEEP, activeforeground="#FFFFFF",
                               relief="groove", font=FONT_BOLD, cursor="hand2", padx=10, pady=4)
        self.b_split.pack(side="left", padx=4)

        # 本页只放“动作按钮”，音乐库/命名库在上面的共用栏里
        dub = tk.LabelFrame(pd, text="🎵 一键配乐 · 给所有 *未分发 文件夹里的视频换音乐（勾选“配乐时改名”才按命名库改文件名） 🎵")
        dub.grid(row=5, column=0, sticky="ew", padx=8, pady=4)
        dub.configure(bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        dub_top = tk.Frame(dub, bg=PANEL)
        dub_top.pack(fill="x")
        self.b_dub = tk.Button(dub_top, text="🎧 一键配乐已分发视频", command=self.on_dub)
        self.b_dub.configure(bg=SEA_DEEP, fg="#FFFFFF", activebackground=CORAL, relief="groove",
                             font=FONT_BOLD, cursor="hand2", padx=10, pady=4)
        self.b_dub.pack(side="left", padx=6, pady=2)
        self.b_dub_cancel = tk.Button(dub_top, text="⏹ 取消", command=self.on_dub_cancel, state="disabled")
        self.b_dub_cancel.configure(bg="#FFFFFF", fg=CORAL, activebackground="#FFE9E6", relief="groove",
                                    font=FONT_BOLD, cursor="hand2", padx=8, pady=4)
        self.b_dub_cancel.pack(side="left", padx=2, pady=2)
        tk.Label(dub_top, text="本页面向“按日期分发”；切到“均分/自定义数量”页配乐只作用于那批文件夹",
                 bg=PANEL, fg=MUTED, font=FONT_MAIN).pack(side="left", padx=8)

        self.status = tk.Label(root, text="🌊 就绪：先选分发位置，再投放素材 ⛵", anchor="w", relief="groove",
                               bg="#FFFFFF", fg=SEA_DEEP, font=FONT_MAIN)
        self.status.grid(row=5, column=0, sticky="ew")
        # 进度条：平时隐藏（grid_remove），执行/配乐时占用第6行，不挤状态栏
        self.progress = ttk.Progressbar(root, mode="determinate")

        # 按日期分发页内部也用 grid：可伸缩的表格区吸收缩放，按钮行不会被裁掉。
        # minsize/height 取最小值，靠权重拉大——否则内容一多就会把底部按钮顶出窗口。
        pd.rowconfigure(0, weight=0)   # 分发位置
        pd.rowconfigure(1, weight=0)   # 类型切换
        pd.rowconfigure(2, weight=0)   # 分发规则
        pd.rowconfigure(3, weight=1, minsize=90)   # 素材 + 计划表（伸缩）
        pd.rowconfigure(4, weight=0)   # 预览/执行/撤回
        pd.rowconfigure(5, weight=0)   # 一键配乐
        pd.rowconfigure(6, weight=1, minsize=34)   # 预览文本（伸缩）
        pd.columnconfigure(0, weight=1)
        self.preview_text = tk.Text(pd, height=2, bg="#FFFFFF", fg=TEXT, insertbackground=SEA_DEEP)
        self.preview_text.grid(row=6, column=0, sticky="nsew", padx=8, pady=4)

        # 均分/自定义数量分发页（同一窗口内的新页面）
        self.split_page = SplitPage(self.page_split, self)
        self.split_page.grid(row=0, column=0, sticky="nsew")

        if HAS_DND:
            try:
                self.listbox.drop_target_register(DND_FILES)
                self.listbox.dnd_bind("<<Drop>>", self.on_drop)
                root.drop_target_register(DND_FILES)
                root.dnd_bind("<<Drop>>", self.on_drop)
            except Exception:
                pass

        # 单启动器、零弹窗启动：只进主界面，所有功能都在界面上点
        self.root.after(200, self.startup_silent)
        # 按两页真实需要的最小高度设置窗口下限（见 _apply_min_size 的说明）
        self.root.after(120, self._apply_min_size)
        # 关窗时先确认：分发/配乐中途退出可能留下"搬一半"的状态
        try:
            root.protocol("WM_DELETE_WINDOW", self.on_close_app)
        except Exception:
            pass

    def _apply_min_size(self):
        """按两页真实的最小需求设置窗口下限。

        为什么不能随便写死 560：页面比窗口矮时，Tk 不会裁剪，而是让页面
        溢出到容器外面，直接盖住下面的音乐库/命名库栏（配乐/改名按钮就"消失"了）。
        所以下限必须 = 固定行高度 + 两页中较高的那页的最小需求高度。
        """
        try:
            self.root.update_idletasks()
            need = 0
            for page in (getattr(self, "page_date", None), getattr(self, "page_split", None)):
                if page is None:
                    continue
                # 页面自己会用 grid 权重收缩，先临时放开再量真实需求
                need = max(need, page.winfo_reqheight())
            chrome = 0
            for c in self.root.grid_slaves():
                r = c.grid_info().get("row")
                if r is not None and int(r) != 3:   # row3 是放页面的容器，不计入
                    chrome += c.winfo_reqheight() + 12
            min_h = int(need + chrome + 8)
            self.root.minsize(800, max(560, min_h))
        except Exception:
            pass

    def on_close_app(self):
        """关窗前的收尾：任务在跑就问清楚；已完成的配置照常保存。"""
        try:
            if self.busy_quiet():
                what = "配乐" if self._dubbing else ("撤回" if self._undoing else ("重命名" if self._rename_busy else "分发"))
                if not messagebox.askyesno(
                        "确认退出",
                        f"正在{what}，现在退出会中断它。\n"
                        "已完成的部分文件不会丢（撤回/移动都有日志），但这一次会停在一半。\n\n确定要退出吗？"):
                    return
            self.persist()
            self.root.destroy()
        except Exception:
            try:
                self.root.destroy()
            except Exception:
                pass

    def _ui(self, fn):
        """子线程回主线程执行 UI 操作。"""
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def _append_text(self, widget, msg):
        def _do():
            try:
                widget.insert(tk.END, msg + "\n")
                widget.see(tk.END)
            except Exception:
                pass
        self._ui(_do)

    def _clear_text(self, widget):
        def _do():
            try:
                widget.delete("1.0", tk.END)
            except Exception:
                pass
        self._ui(_do)

    def _append_preview(self, msg):
        self._append_text(self.preview_text, msg)

    def _progress_start(self, maximum):
        def _do():
            try:
                self.progress.configure(maximum=max(1, int(maximum)), value=0)
                # 独占第6行：显示/隐藏都不影响状态栏和任何按钮
                self.progress.grid(row=6, column=0, sticky="ew", padx=8, pady=(2, 0))
            except Exception:
                pass
        self._ui(_do)

    def _progress_set(self, value, total=None):
        # total 形参必须留着：make_throttled_progress 会按 (done, total) 两参调用它
        # （分发/撤回/配乐都走这条路）。以前只收一个 value，导致配乐子线程
        # 每转完一个就抛 "takes 2 positional arguments but 3 were given"，
        # 表现为"一键配乐全部失败"而界面只停在"配乐中..."。
        def _do():
            try:
                if total:
                    try:
                        self.progress.configure(maximum=max(1, int(total)))
                    except Exception:
                        pass
                self.progress.configure(value=value)
            except Exception:
                pass
        self._ui(_do)

    def _progress_done(self):
        def _do():
            try:
                self.progress.grid_remove()
            except Exception:
                pass
        self._ui(_do)

    def on_media_change(self):
        self.persist()
        self.set_status(f"已切换到：{self.media_type.get()}，重新投放后生效")

    def startup_silent(self):
        """静默启动：绝不弹窗。旧路径不存在就清空等用户自己点“浏览...”，向导改由按钮手动触发。"""
        try:
            out = self.output_root.get()
            if out and not os.path.exists(out):
                self.output_root.set("")
                self.persist()
                self.set_status("🌊 就绪：分发位置需重新选（旧路径不存在），点“浏览...”选一个，然后投放素材")
            elif not out:
                self.set_status("🌊 就绪：先点“浏览...”选分发位置，再投放素材")
            else:
                self.set_status("🌊 就绪：投放素材后点“自动生成计划”")
        except Exception:
            pass
        # 后台：音乐库纠正 + 静默拉起 Ollama + 刷新状态，全程无弹窗
        try:
            threading.Thread(target=self._ensure_backend_silent, daemon=True).start()
        except Exception:
            pass

    def _ensure_backend_silent(self):
        # Tk 变量先在主线程读出来（这里可能跑在子线程上）
        try:
            cur_md = self.music_dir.get()
            cur_nd = self.name_dir.get()
        except Exception:
            return
        try:
            fixed = autofix_music_dir(cur_md)
            if fixed and fixed != cur_md:
                def _apply_md():
                    try:
                        self.music_dir.set(fixed)
                        # 只在内存里纠正，不写回配置：否则移动硬盘/网络盘临时掉线时，
                        # 用户的真实音乐库路径会被悄悄改成本地默认目录，再也回不去
                        self.set_status(f"⚠原音乐库不存在（{cur_md}），本次临时用：{fixed}\n"
                                        "配置未改，盘接回来后下次启动自动切回")
                    except Exception:
                        pass
                self._ui(_apply_md)
        except Exception:
            pass
        try:
            fixed_n = autofix_name_dir(cur_nd)
            if fixed_n and fixed_n != cur_nd:
                def _apply_nd():
                    try:
                        self.name_dir.set(fixed_n)
                        self.set_status(f"⚠原命名库不存在（{cur_nd}），本次临时用：{fixed_n}\n"
                                        "配置未改，路径恢复后下次启动自动切回")
                    except Exception:
                        pass
                self._ui(_apply_nd)
        except Exception:
            pass
        # 配乐不再依赖本地大模型，不再静默拉起 Ollama
        # 回主线程刷新状态行
        try:
            self._ui(self.refresh_llm_status)
        except Exception:
            try:
                self.root.after(0, self.refresh_llm_status)
            except Exception:
                pass

    def startup_wizard(self):
        # 保留给“重设向导”按钮手动调用；开机不再自动调用，避免弹窗打扰
        # 保存的路径在本机不存在（比如整个文件夹拷到新电脑），视为没选，重新选一次
        if not self.output_root.get() or not os.path.exists(self.output_root.get()):
            messagebox.showinfo("第1步", "请选择分发位置文件夹（例如下载目录）")
            self.choose_output()
        dlg = WizardDialog(self.root, parse_start_date(self.e_start.get()),
                           safe_int_var(self.per_group, 15), safe_int_var(self.interval, 0))
        self.root.wait_window(dlg)
        if dlg.result:
            d, per, inv = dlg.result
            self.start_date_var.set(d.strftime("%Y-%m-%d"))
            self.per_group.set(per)
            self.interval.set(inv)
            self.persist()
            self.set_status(f"已设置：从{d}开始，每组{per}个，间隔{inv}天。请投放后点自动生成计划。")

    def show_wizard(self):
        dlg = WizardDialog(self.root, parse_start_date(self.e_start.get()),
                           safe_int_var(self.per_group, 15), safe_int_var(self.interval, 0))
        self.root.wait_window(dlg)
        if dlg.result:
            d, per, inv = dlg.result
            self.start_date_var.set(d.strftime("%Y-%m-%d"))
            self.per_group.set(per)
            self.interval.set(inv)
            self.auto_plan()

    def _rel(self, path):
        """将程序目录下的路径转为相对路径，便于便携迁移"""
        try:
            if not path:
                return path
            appd = get_app_dir()
            if os.path.normcase(os.path.normpath(path)).startswith(os.path.normcase(os.path.normpath(appd)) + os.sep):
                return os.path.relpath(path, appd)
        except Exception:
            pass
        return path

    def persist(self):
        try:
            save_config({"output_root": self.output_root.get(),
                         "start_date": self.e_start.get(),
                         "per_group": safe_int_var(self.per_group, 15),
                         "interval": safe_int_var(self.interval, 0),
                         "rename": self.do_rename.get(),
                         "media_type": self.media_type.get(),
                         "shuffle": self.shuffle.get(),
                         "music_dir": self._rel(self.music_dir.get()),
                         "remove_original": self.remove_original.get(),
                         "dub_rename": self.dub_rename.get(),
                         "name_dir": self._rel(self.name_dir.get()),
                         "split_mode": self.split_mode.get(),
                         "split_n": safe_int_var(self.split_n, 4),
                         "split_prefix": self.split_prefix.get(),
                         "split_suffix": self.split_suffix.get(),
                         "page": getattr(self, "_page", "date")})
        except Exception:
            pass

    def choose_music(self):
        try:
            init = self.music_dir.get() or get_app_dir()
        except Exception:
            init = ""
        d = filedialog.askdirectory(title="选择音乐库文件夹", initialdir=init or None)
        if d:
            self.music_dir.set(d)
            self.persist()

    def choose_name_dir(self):
        """选命名库：直接挑 TXT 文件（能按 *.txt 筛选），多选时取它们的共同父目录。

        以前这里用的是"选文件夹"，对话框里只有目录树、根本没有文件列表，
        所以没法筛出 TXT。命名库本来就是"TXT 每行一个命名"，挑文件更直观；
        只挑一个就存该文件，挑多个就存共同父目录（等价于老行为）。
        要选整个文件夹用旁边的【选文件夹】。
        """
        try:
            init = self.name_dir.get() or get_app_dir()
        except Exception:
            init = ""
        if init and os.path.isfile(init):
            init = os.path.dirname(init)
        try:
            fs = filedialog.askopenfilenames(
                title="选命名库TXT（可多选；选多个=它们所在的文件夹）",
                initialdir=init or None,
                filetypes=[("TXT 命名文件", "*.txt"), ("所有文件", "*.*")])
        except Exception:
            return
        if not fs:
            return
        fs = [os.path.abspath(f) for f in fs]
        if len(fs) == 1:
            self.name_dir.set(fs[0])
        else:
            parents = {os.path.dirname(f) for f in fs}
            if len(parents) == 1:
                self.name_dir.set(parents.pop())
            else:
                self.name_dir.set(fs[0])
        self.persist()
        try:
            self.refresh_llm_status()
        except Exception:
            pass

    def choose_name_folder(self):
        """选整个命名库文件夹（老行为，文件夹里所有 TXT 一起读）。"""
        try:
            init = self.name_dir.get() or get_app_dir()
        except Exception:
            init = ""
        if init and os.path.isfile(init):
            init = os.path.dirname(init)
        d = filedialog.askdirectory(title="选命名库文件夹（里面所有TXT一起读，每行一个命名）",
                                    initialdir=init or None)
        if d:
            self.name_dir.set(os.path.abspath(d))
            self.persist()
            try:
                self.refresh_llm_status()
            except Exception:
                pass

    def refresh_llm_status(self):
        # 状态检查涉及扫音乐库/命名库+ffmpeg，放后台线程做，避免卡主界面
        # Tk 变量必须在主线程读：子线程读会抛 “not in main loop”
        try:
            if threading.current_thread() is not threading.main_thread():
                self._ui(self.refresh_llm_status)
                return
            mdir = self.music_dir.get()
            ndir = self.name_dir.get()
        except Exception:
            return
        threading.Thread(target=self._refresh_llm_status_bg, args=(mdir, ndir), daemon=True).start()

    def _apply_lib_status(self, txt):
        """共用栏状态文字（主线程调用）。"""
        try:
            self.lbl_llm.config(text=txt)
        except Exception:
            pass

    def _refresh_llm_status_bg(self, mdir=None, ndir=None):
        try:
            if mdir is None or ndir is None:
                mdir = self.music_dir.get()
                ndir = self.name_dir.get()
            n = len(list_audios(mdir))
            nn = len(load_names(ndir))
            ff_ok = ffmpeg_available()
            txt = f"音乐{n}首 · 命名{nn}条 · ffmpeg:{'可用' if ff_ok else '缺失'}"
        except Exception:
            return

        def _apply():
            self._apply_lib_status(txt)
        self._ui(_apply)

    def choose_output(self):
        d = filedialog.askdirectory(title="选择分发位置文件夹")
        if d:
            self.output_root.set(d)
            self.persist()

    def open_output(self):
        d = self.output_root.get()
        if d and os.path.exists(d):
            os.startfile(d)
        else:
            messagebox.showwarning("提示", "分发位置还不存在或未选择")

    def _style_page_btns(self):
        """高亮当前页按钮。"""
        try:
            on = {"bg": CORAL, "fg": "#FFFFFF"}
            off = {"bg": "#FFFFFF", "fg": SEA_DEEP}
            self.btn_page_date.configure(**(on if self._page == "date" else off))
            self.btn_page_split.configure(**(on if self._page == "split" else off))
        except Exception:
            pass

    def show_page(self, name):
        """切换页面：同一个窗口内两页，不新开窗口。
        有任务在跑时不允许切（否则进度条/弹框跟着页面被隐藏，用户以为卡死）。"""
        if name not in ("date", "split"):
            return
        if name != getattr(self, "_page", "date") and self.busy_quiet():
            self.set_status("正在执行操作，请等它完成再切页面")
            return
        try:
            self.persist()
        except Exception:
            pass
        self._page = name
        for pg, is_split in ((self.page_date, False), (self.page_split, True)):
            try:
                if (name == "split") == is_split:
                    pg.grid(row=0, column=0, sticky="nsew")
                else:
                    pg.grid_remove()
            except Exception:
                pass
        self._style_page_btns()
        if name == "split":
            self.split_page.on_shown()

    def busy_quiet(self):
        """是否正在忙（不弹窗版，给页面切换用）。"""
        return (getattr(self, "_executing", False) or getattr(self, "_dubbing", False)
                or getattr(self, "_undoing", False) or getattr(self, "_rename_busy", False)
                or getattr(getattr(self, "split_page", None), "_executing", False))

    def open_split(self):
        """（底部按钮）等同于切到“均分/自定义数量”页。"""
        self.show_page("split")

    def busy(self, what="这个操作"):
        """两页共用一个“忙”标志：分发/配乐/撤回任一在跑，别再并发动手。
        返回 True 表示现在不能做。"""
        if getattr(self, "_executing", False) or getattr(self, "_dubbing", False) \
                or getattr(self, "_undoing", False) or getattr(self.split_page, "_executing", False):
            messagebox.showinfo("提示", f"正在执行其它操作（分发/配乐/撤回），请等它完成再{what}")
            return True
        return False

    def refresh_video_list(self):
        self.listbox.delete(0, tk.END)
        for i, v in enumerate(self.videos, 1):
            parent = os.path.basename(os.path.dirname(v))
            self.listbox.insert(tk.END, f"{i}. {parent}/{os.path.basename(v)}")
        self.lbl_count.config(text=f"共{len(self.videos)}个")

    def apply_order(self):
        """按当前开关整理顺序：关=按文件夹名→文件名顺序，开=打乱"""
        if self.shuffle.get():
            random.shuffle(self.videos)
        else:
            self.videos = sort_files(self.videos)

    def on_toggle_shuffle(self):
        self.persist()
        if not self.videos:
            return
        self.apply_order()
        self.refresh_video_list()
        mode = "打乱" if self.shuffle.get() else "顺序"
        self.set_status(f"已切换为{mode}分发")
        self.preview()

    def on_reshuffle(self):
        if not self.videos:
            messagebox.showinfo("提示", "先投放素材再打乱")
            return
        if not self.shuffle.get():
            self.shuffle.set(True)
            self.persist()
        random.shuffle(self.videos)
        self.refresh_video_list()
        self.set_status("已重新打乱一批")
        self.preview()

    def add_videos_from_paths(self, paths):
        vids = collect_files(paths, self.allowed())
        if not vids:
            messagebox.showinfo("提示", "没找到对应类型文件（切换分发类型试试）")
            return
        # Windows 不区分大小写：用 normcase 去重，避免同文件大小写不同重复投放
        uniq = {}
        for x in list(self.videos) + list(vids):
            uniq.setdefault(os.path.normcase(os.path.normpath(x)), x)
        merged = list(uniq.values())
        self.videos = sort_files(merged)
        if self.shuffle.get():
            random.shuffle(self.videos)
            self.set_status(f"已投放{len(self.videos)}个（已打乱）")
        else:
            self.set_status(f"已投放{len(self.videos)}个（已按文件夹名→文件名排序）")
        self.refresh_video_list()
        # 追加投放不再整表重算：用户手工微调过的日期/数量会被 auto_plan 覆盖丢失。
        # 计划为空才自动生成；已有计划则保留手工值，余量由预览/执行的自动顺延组兜住。
        if not self.plan:
            self.auto_plan(silent=True)
        else:
            self.refresh_plan()
            self.persist()
            self.preview()

    def on_drop(self, event):
        # 拖到均分页上时，交给那一页的素材列表（共用时间戳，不会重复处理）
        if getattr(self, "_page", "date") == "split":
            try:
                self.split_page.on_drop(event)
                return
            except Exception as e:
                # 不能静默吞掉后落到本页：用户会以为素材进了均分页，实际进了按日期页
                self.set_status(f"拖放处理失败：{e}")
                messagebox.showwarning("提示", f"拖放处理失败：\n{e}")
                return
        # 防抖：listbox 和 root 同时注册时，拖到 listbox 会触发两次
        now = int(time.time() * 1000)
        if now - getattr(self, "_last_drop_ms", 0) < 300:
            return
        self._last_drop_ms = now
        # 优先用 Tk 的 splitlist（正确处理空格/中文/括号），失败回退正则
        paths = []
        try:
            parts = self.root.splitlist(event.data)
            paths = [str(p).strip().strip("{}").strip('"').strip("'") for p in parts if str(p).strip()]
        except Exception:
            parts = re.findall(r"\{.*?\}|\S+", event.data or "")
            paths = [p.strip().strip("{}").strip('"').strip("'") for p in parts]
        paths = [p for p in paths if p]
        if paths:
            self.add_videos_from_paths(paths)

    def add_files(self):
        fs = filedialog.askopenfilenames(title="选择文件", filetypes=self.file_dialog_types())
        if fs:
            self.add_videos_from_paths(list(fs))

    def add_folder(self):
        d = filedialog.askdirectory(title="选择素材文件夹（含子文件夹）")
        if d:
            self.add_videos_from_paths([d])

    def clear_videos(self):
        self.videos = []
        self.refresh_video_list()
        self.plan = []
        self.refresh_plan()
        try:
            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert(tk.END, "已清空，请重新投放素材。\n")
        except Exception:
            pass

    def auto_plan(self, silent=False):
        if not self.videos:
            if not silent:
                messagebox.showinfo("提示", "先投放素材再生成计划")
            return
        if not is_valid_date_str(self.e_start.get()):
            messagebox.showwarning("提示", "起始日期格式不对，用 2026-09-08 或 09-08")
            return
        try:
            d = parse_start_date(self.e_start.get())
            per = max(1, safe_int_var(self.per_group, 15))
            inv = max(0, safe_int_var(self.interval, 0))
        except Exception:
            messagebox.showwarning("提示", "起始日期/数量/间隔格式不对")
            return
        try:
            self.plan = build_plan(len(self.videos), per, d, inv)
        except Exception as e:
            # 间隔填得过大等极端输入不能让程序崩，给出可操作的提示
            messagebox.showwarning("提示", f"生成计划失败：{e}\n试试把“间隔天数”改小一些（最大 {MAX_INTERVAL_DAYS} 天）")
            return
        self.refresh_plan()
        self.persist()
        self.set_status(f"已生成{len(self.plan)}组计划，共{sum(r['count'] for r in self.plan)}个")
        self.preview()

    def refresh_plan(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for r in self.plan:
            self.tree.insert("", tk.END, values=(f"{r['date']:%m-%d}未分发 ({r['date']})", r["count"]))
        self.lbl_plan.config(text=f"计划{sum(r['count'] for r in self.plan)}个/{len(self.videos)}待分")

    def plan_add(self):
        if not is_valid_date_str(self.e_start.get()):
            messagebox.showwarning("提示", "起始日期格式不对，用 2026-09-08 或 09-08")
            return
        d = parse_start_date(self.e_start.get())
        inv = max(0, min(MAX_INTERVAL_DAYS, safe_int_var(self.interval, 0)))
        per = max(1, safe_int_var(self.per_group, 15))
        try:
            if self.plan:
                d = self.plan[-1]["date"] + datetime.timedelta(days=inv + 1)
        except (OverflowError, ValueError):
            messagebox.showwarning("提示", f"日期已经超出范围了，换个小的“间隔天数”试试（最大 {MAX_INTERVAL_DAYS} 天）")
            return
        self.plan.append({"date": d, "count": per})
        self.refresh_plan()
        self.preview()

    def plan_del(self):
        sel = self.tree.selection()
        if not sel:
            return
        idxs = sorted([self.tree.index(s) for s in sel], reverse=True)
        for i in idxs:
            del self.plan[i]
        self.refresh_plan()
        self.preview()

    def plan_edit(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        i = self.tree.index(sel[0])
        row = self.plan[i]
        from tkinter import simpledialog
        new_date = simpledialog.askstring("改日期", "格式如 2026-09-10", initialvalue=row["date"].strftime("%Y-%m-%d"))
        if new_date:
            # 无效输入原来静默保留旧值，用户以为改了其实没改；现在明确提示
            if not is_valid_date_str(new_date):
                messagebox.showwarning("提示", "日期格式不对，用 2026-09-10 或 09-10，保持原日期不变")
            else:
                row["date"] = parse_start_date(new_date, row["date"])
        new_c = simpledialog.askstring("改数量", "该组放几个", initialvalue=str(row["count"]))
        if new_c:
            try:
                c = int(str(new_c).strip())
                if c < 1:
                    raise ValueError
                row["count"] = c
            except Exception:
                messagebox.showwarning("提示", "数量必须是大于0的整数，保持原数量不变")
        self.refresh_plan()
        self.preview()

    def current_allocations(self):
        return allocate(self.videos, self.plan)

    def preview(self):
        self.preview_text.delete("1.0", tk.END)
        if not self.videos or not self.plan:
            self.preview_text.insert(tk.END, "先投放素材并生成计划后预览。\n")
            return
        total_plan = sum(r["count"] for r in self.plan)
        warn = ""
        if total_plan < len(self.videos):
            warn = f"⚠计划{total_plan}个 < 素材{len(self.videos)}个，剩余{len(self.videos)-total_plan}个将自动顺延多建一组。\n"
        elif total_plan > len(self.videos):
            warn = f"⚠计划{total_plan}个 > 素材{len(self.videos)}个，最后一组只取剩下的。\n"
        self.preview_text.insert(tk.END, warn)
        mode = "🌊 打乱分发" if self.shuffle.get() else "⛵ 顺序分发"
        self.preview_text.insert(tk.END, f"当前模式：{mode}\n")
        allocs = self.current_allocations()
        allocs, appended = ensure_tail_group(
            self.videos, allocs, self.plan,
            lambda: parse_start_date(self.e_start.get()),
            safe_int_var(self.interval, 0))
        if appended:
            self.preview_text.insert(tk.END, f"将自动追加一组 {allocs[-1][1]}（{len(allocs[-1][2])}个）。\n")
        rw = self.rename_word()
        if self.media_type.get() == "all":
            rw = "auto"
        for dt, fname, files in allocs:
            self.preview_text.insert(tk.END, f"\n【{fname}】{dt} 共{len(files)}个\n")
            for idx, f in enumerate(files[:8], 1):
                parent = os.path.basename(os.path.dirname(f))
                if self.do_rename.get():
                    ext = os.path.splitext(f)[1]
                    w = _rename_word_for_file(rw, f)
                    self.preview_text.insert(tk.END, f"  - {parent}/{os.path.basename(f)} -> {fname}{w}{idx}{ext}\n")
                else:
                    self.preview_text.insert(tk.END, f"  - {parent}/{os.path.basename(f)}\n")
            if len(files) > 8:
                self.preview_text.insert(tk.END, f"  ...还有{len(files)-8}个\n")
        self.set_status(f"预览完成：{len(allocs)}组")

    def execute(self):
        if self.busy("分发"):
            return
        out = self.output_root.get()
        if not out:
            messagebox.showwarning("提示", "先选择分发位置文件夹")
            return
        if not self.videos or not self.plan:
            messagebox.showwarning("提示", "先投放素材并生成计划")
            return
        try:
            os.makedirs(out, exist_ok=True)
        except Exception as e:
            messagebox.showwarning("提示", f"分发位置建目录失败（检查权限/磁盘）：\n{out}\n{e}")
            return
        # 执行前强制刷新一次预览，避免“加一行后没点预览就执行”看到的是旧明细
        try:
            self.preview()
        except Exception:
            pass
        allocs = self.current_allocations()
        allocs, _ = ensure_tail_group(
            self.videos, allocs, self.plan,
            lambda: parse_start_date(self.e_start.get()),
            safe_int_var(self.interval, 0))
        summary = "\n".join([f"{fn}：{len(fs)}个" for _, fn, fs in allocs])
        rw = "auto" if self.media_type.get() == "all" else self.rename_word()
        rn = "，并改名为 文件夹名+视频/图片+序号" if (self.do_rename.get() and rw == "auto") else (f"，并改名为 文件夹名+{rw}+序号" if self.do_rename.get() else "")
        if not messagebox.askyesno("确认执行", f"将移动{sum(len(fs) for _,_,fs in allocs)}个文件到：\n{out}\n\n{summary}\n\n同名文件夹合并{rn}，确定吗？"):
            return
        self._executing = True
        try:
            self.b_exec.config(state="disabled")
        except Exception:
            pass
        self.set_status("执行中...")
        self.persist()
        total = sum(len(fs) for _, _, fs in allocs)
        self._progress_start(total)
        # Tk 变量必须在主线程读完再进线程，子线程里读会抛 “not in main loop”
        do_rename = self.do_rename.get()

        def _run_body_exec():
            # 移动放子线程：跨盘大文件是 copy+delete，主线程跑会把窗口卡成“未响应”
            moved, rows = execute_move(allocs, out, rename=do_rename, rename_word=rw,
                                       progress=make_throttled_progress(self._progress_set))
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            # 同一秒内连跑两次会撞名覆盖日志，撤回全靠日志，撞了就自动加 _1
            logp = unique_dest(os.path.join(out, f"distribute_log_{ts}.csv"))
            log_err = None
            try:
                with open(logp, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["原路径", "新路径", "日期文件夹"])
                    w.writerows(rows)
            except Exception as e:
                log_err = e

            def _done():
                self._executing = False
                self._progress_done()
                try:
                    self.b_exec.config(state="normal")
                except Exception:
                    pass
                moved_srcs = {r[0] for r in rows if not r[1].startswith("FAILED") and not r[1].startswith("SKIPPED:源文件不存在")}
                # SKIPPED（已在目标/同路径）也算已处理，从待分移除；源不存在的保留以便用户检查
                self.videos = [v for v in self.videos if v not in moved_srcs]
                self.refresh_video_list()
                self.refresh_plan()
                skipped = sum(1 for r in rows if r[1].startswith("SKIPPED"))
                failed = sum(1 for r in rows if r[1].startswith("FAILED"))
                self.set_status(f"完成：移动{moved}个，跳过{skipped}个，失败{failed}个，日志{os.path.basename(logp)}")
                if log_err is not None:
                    # 日志写不进去 = 撤回不了，必须让用户知道，不能静默吞掉
                    messagebox.showwarning("警告", f"分发完成了，但日志写入失败：\n{logp}\n{log_err}\n本次分发将无法撤回，请检查磁盘空间/权限。")
                messagebox.showinfo("完成", f"已移动{moved}个到 {out}\n跳过{skipped}个，失败{failed}个\n日志：{os.path.basename(logp)}")
                try:
                    os.startfile(out)
                except Exception:
                    pass
            self._ui(_done)

        def run():
            # 外壳：任何未预料的异常都要把 _executing 清掉并恢复按钮，
            # 否则之后所有分发/配乐/撤回都被自己的 busy 判断挡住，界面像死了。
            try:
                _run_body_exec()
            except Exception as e:
                self._exec_failed("分发过程出错", e)

        threading.Thread(target=run, daemon=True).start()

    def _exec_failed(self, what, e):
        """分发类操作内部错误：恢复标志/按钮 + 明确提示。"""
        def _do():
            try:
                self._executing = False
                self.b_exec.config(state="normal")
            except Exception:
                pass
            self._progress_done()
            try:
                self.set_status(f"{what}：{e}")
            except Exception:
                pass
            try:
                messagebox.showerror(what, f"{e}\n\n已处理完的文件不受影响；"
                                           "如果日志已写出，可点“撤回上次”回退。")
            except Exception:
                pass
        self._ui(_do)

    def on_undo(self):
        out = self.output_root.get()
        if not out or not os.path.exists(out):
            messagebox.showwarning("提示", "先选择有效的分发位置文件夹")
            return
        if getattr(self, "_executing", False) or getattr(self, "_dubbing", False):
            messagebox.showinfo("提示", "分发/配乐正在进行中，请等它结束再撤回")
            return
        if getattr(self.split_page, "_executing", False):
            messagebox.showinfo("提示", "均分页正在分发中，请等它结束再撤回")
            return
        logp, n = latest_restorable_log(out)
        if not logp:
            messagebox.showinfo("提示", "没找到可撤回的分发日志")
            return
        if n == 0:
            messagebox.showinfo("提示", f"最近的分发日志 {os.path.basename(logp)} 里没有可撤回的文件\n（文件可能已被手动删除或移走）")
            return
        if not messagebox.askyesno("撤回确认", f"将按日志撤回{n}个文件：\n{os.path.basename(logp)}\n把文件移回原位置并恢复原文件名（含配乐过的一起退），确定吗？"):
            return
        # 撤回会动到正在被写/被转码的文件，不能和分发、配乐并发
        self._undoing = True
        self.set_status("撤回中...")
        self._progress_start(n)

        def run():
            try:
                res = undo_distribute(logp, out)
            except Exception as e:
                def _fail():
                    self._undoing = False
                    self._progress_done()
                    self.set_status(f"撤回出错：{e}")
                    messagebox.showwarning("撤回出错", f"撤回过程中出错，已撤回部分文件：\n{e}")
                self._ui(_fail)
                return
            ok, fail, dubbed, from_backup, _marked, retry_log, renamed_back = res

            def _done():
                self._undoing = False
                self._progress_done()
                self.set_status(f"撤回完成：{ok}个，失败{fail}个")
                msg = f"已撤回{ok}个，失败{fail}个"
                if from_backup:
                    msg += f"\n其中{from_backup}个用配乐前原片恢复"
                if dubbed:
                    msg += f"\n其中{dubbed}个是配乐后版本（当时无原片备份，位置和文件名已恢复）"
                if renamed_back:
                    msg += (f"\n⚠有{len(renamed_back)}个因原位置已被占用，退回时改了名："
                            + "、".join(renamed_back[:5]) + ("…" if len(renamed_back) > 5 else ""))
                if retry_log:
                    msg += f"\n失败的{fail}个已另存 {os.path.basename(retry_log)}，关掉占用它的程序后再点“撤回上次”即可重试"
                else:
                    msg += "\n相关日志已标记.undone"
                messagebox.showinfo("撤回完成", msg)
                try:
                    os.startfile(out)
                except Exception:
                    pass
            self._ui(_done)

        threading.Thread(target=run, daemon=True).start()

    def on_rename_existing(self):
        out = self.output_root.get()
        if not out or not os.path.exists(out):
            messagebox.showwarning("提示", "先选择有效的分发位置文件夹")
            return
        if self.busy("重命名"):
            return
        rw = self.rename_word()
        if not messagebox.askyesno("重命名确认", f"将把 {out} 下所有 *未分发 文件夹内的文件\n改名为 文件夹名+{rw}+序号，确定吗？"):
            return
        allowed = self.allowed()
        rww = "auto" if self.media_type.get() == "all" else rw
        self.set_status("重命名已分发中...")
        self._progress_start(1)
        self._rename_busy = True

        def run():
            # 放子线程：文件多时 os.rename 几千次，主线程会卡成"未响应"
            try:
                renamed, rows = rename_existing(out, allowed_exts=allowed, rename_word=rww,
                                                progress=lambda d: self._progress_set(d))
            except Exception as e:
                def _fail():
                    self._rename_busy = False
                    self._progress_done()
                    self.set_status(f"重命名出错：{e}")
                    messagebox.showwarning("重命名出错", f"{e}")
                self._ui(_fail)
                return
            logp = None
            log_err = None
            if renamed:
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                logp = unique_dest(os.path.join(out, f"distribute_log_rename_{ts}.csv"))
                try:
                    with open(logp, "w", newline="", encoding="utf-8-sig") as f:
                        w = csv.writer(f)
                        w.writerow(["原路径", "新路径", "日期文件夹"])
                        w.writerows(rows)
                except Exception as e:
                    log_err = e
            failed = sum(1 for r in rows if str(r[1]).startswith("FAILED"))

            def _done():
                self._rename_busy = False
                self._progress_done()
                self.set_status(f"重命名完成：{renamed}个")
                msg = f"已重命名{renamed}个"
                if failed:
                    msg += f"\n失败{failed}个（看日志里的 FAILED 行）"
                if log_err is not None:
                    # 日志写不进去 = 撤不回改名，必须明说
                    messagebox.showwarning("警告", f"改名完成了，但日志写入失败：\n{logp}\n{log_err}\n"
                                                  "这次改名将无法通过“撤回上次”退回原名。")
                messagebox.showinfo("完成", msg)
            self._ui(_done)
        threading.Thread(target=run, daemon=True).start()

    def on_dub_cancel(self):
        """配乐取消：只停掉还没开始的，正在转码的会做完当前文件（保证不断尾不断链）。"""
        try:
            self._dub_cancel.set()
            self.set_status("正在取消配乐（等当前转码收尾）...")
        except Exception:
            pass

    def on_dub(self, folders=None, log_widget=None, status_cb=None):
        """folders 给定时只配乐这些文件夹（均分页调用），
        否则保持老规矩：扫分发位置下所有含“未分发”的文件夹。
        log_widget/status_cb 用来把进度写到调用方自己的页面，缺省用主界面预览区。"""
        if self.busy("配乐"):
            return
        out = self.output_root.get()
        if not out or not os.path.exists(out):
            messagebox.showwarning("提示", "先选择有效的分发位置文件夹")
            return
        mdir = self.music_dir.get()
        audios = list_audios(mdir)
        if not audios:
            messagebox.showwarning("提示", f"音乐库没有音频：{mdir}")
            return
        ff = ffmpeg_path()
        if ff == "ffmpeg":
            if not shutil.which("ffmpeg"):
                messagebox.showwarning("提示", "没找到ffmpeg.exe，请把它和exe放同一目录")
                return
        # 只处理已分发视频（自然排序，与分发排序一致；跳过崩溃残留的临时半成品）
        scan_dirs = []
        if folders:
            for p in folders:
                try:
                    p = os.path.abspath(str(p))
                except Exception:
                    continue
                if os.path.isdir(p):
                    scan_dirs.append(p)
        else:
            for d in sorted(os.listdir(out), key=natsort_key):
                if d.startswith(DUB_BACKUP_PREFIX) or d.startswith("."):
                    continue
                p = os.path.join(out, d)
                if os.path.isdir(p) and "未分发" in d:
                    scan_dirs.append(p)
        targets = []
        for p in scan_dirs:
            try:
                fns = sorted(os.listdir(p), key=natsort_key)
            except Exception:
                continue
            for fn in fns:
                if is_temp_media_name(fn):
                    continue
                fp = os.path.join(p, fn)
                if os.path.isfile(fp) and os.path.splitext(fn)[1].lower() in VIDEO_EXTS:
                    targets.append(fp)
        # 跳过已配过乐的成品（含配乐后又被重命名的，沿重命名链追踪），避免二次配乐+二次改名
        try:
            dubbed = dubbed_live_set(out)
        except Exception:
            dubbed = set()
        before_n = len(targets)
        targets = [t for t in targets
                   if os.path.normcase(os.path.normpath(t)) not in dubbed]
        skipped_dub = before_n - len(targets)
        if not targets:
            messagebox.showinfo("提示", "没有需要配乐的视频" + ("（之前配过乐的已自动跳过）" if skipped_dub else ""))
            return
        do_rename = self.dub_rename.get()
        names = []
        if do_rename:
            names = load_names(self.name_dir.get())
            if not names:
                messagebox.showwarning("提示", f"已勾选“改名”，但命名库没有可用的TXT命名：\n{self.name_dir.get()}\n请在命名库文件夹放TXT（每行一个命名），或取消“改名”勾选。")
                return
        rm = self.remove_original.get()
        skip_note = f"\n已自动跳过{skipped_dub}个配过乐的" if skipped_dub else ""
        mode_txt = (f"并按命名库{len(names)}条命名随机改名（不重复）" if do_rename else "，不改文件名（保留原名）")
        if not messagebox.askyesno("一键配乐", f"将给{len(targets)}个已分发视频换上新音乐{mode_txt}\n音乐库{len(audios)}首，{'去掉原声' if rm else '混音保留原声'}\n（{DUB_WORKERS}路并发，中途可取消）\n（原片自动备份，随时可撤回）{skip_note}"):
            return
        self.persist()
        self._dubbing = True
        try:
            self._dub_cancel.clear()
        except Exception:
            pass
        try:
            self.b_dub.config(state="disabled")
            self.b_dub_cancel.config(state="normal")
        except Exception:
            pass
        self.set_status("配乐中...")
        sink = log_widget if log_widget is not None else self.preview_text
        if status_cb:
            try:
                status_cb("配乐中...")
            except Exception:
                pass
        self._clear_text(sink)
        self._append_text(sink, f"开始配乐 {len(targets)}个...")
        self._progress_start(len(targets))

        def _run_body():
            ok = fail = 0
            rows = []
            dbg = [f"ffmpeg={ff}", f"audios={len(audios)}", f"targets={len(targets)}",
                   f"do_rename={do_rename}", f"names={len(names)}",
                   f"remove_original={rm}"]
            def ui(msg):
                # 子线程禁止直操 Tk，一律经 after 回主线程
                self._append_text(sink, msg)
                self.set_status(msg[:80])
                if status_cb:
                    try:
                        status_cb(msg[:80])
                    except Exception:
                        pass

            # 保证音频尽量不一样：先打乱
            pool = audios[:]
            random.shuffle(pool)
            used_music = set()
            # 命名库也先打乱，改名时随机不重复取
            name_pool = names[:]
            random.shuffle(name_pool)
            used_names = set()
            ts0 = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = os.path.join(out, f"{DUB_BACKUP_PREFIX}{ts0}")
            used_dests = set()
            used_backups = set()
            futs = {}
            cancelled = False
            done_n = 0
            _bump_lock = threading.Lock()

            def _bump(n=1):
                # worker 线程会并发调用，done_n 累加必须加锁
                nonlocal done_n
                with _bump_lock:
                    done_n += n
                self._progress_set(done_n)

            def _offline_music():
                avail = [a for a in pool if a not in used_music] or pool
                m = random.choice(avail) if avail else None
                if m:
                    used_music.add(m)
                return m

            def _offline_name():
                avail = [n for n in name_pool if n not in used_names] or name_pool
                nm = random.choice(avail) if avail else None
                if nm:
                    used_names.add(nm)
                return nm

            # ---- 无LLM：直接规划+提交转码（改名才用命名库，否则原地换音轨保留原名） ----
            from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError
            cancelled_n = 0

            def _work(t):
                vp, music, start = t["vp"], t["music"], t["start"]
                dest, backup = t["dest"], t["backup"]
                inplace = t.get("inplace", False)
                tmp = None
                try:
                    tmp, err = dub_one_video(vp, music, start, rm, ff)
                    if not tmp:
                        raise RuntimeError(err or "转码失败")
                    if inplace:
                        # 原片先备份，再把成品写回原路径（保留原文件名）
                        try:
                            if not os.path.isdir(backup_dir):
                                os.makedirs(backup_dir, exist_ok=True)
                            shutil.move(vp, backup)
                        except Exception as e:
                            raise RuntimeError(f"备份原片失败：{e}")
                        try:
                            shutil.move(tmp, vp)
                            tmp = None
                        except Exception as e:
                            # 回滚：把备份放回原路径，避免文件丢失
                            try:
                                if os.path.exists(backup) and not os.path.exists(vp):
                                    shutil.move(backup, vp)
                            except Exception:
                                pass
                            raise RuntimeError(f"写回原片失败：{e}")
                        return (vp, vp, os.path.basename(os.path.dirname(vp)) + "|" + os.path.basename(music), backup)
                    # 改名模式：tmp 与 dest 同盘，先搬成品再备份原片
                    try:
                        shutil.move(tmp, dest)
                        tmp = None
                    except Exception as e:
                        raise RuntimeError(f"搬运成品失败：{e}")
                    try:
                        if not os.path.isdir(backup_dir):
                            os.makedirs(backup_dir, exist_ok=True)
                        shutil.move(vp, backup)
                    except Exception as e:
                        raise RuntimeError(f"成品已生成但备份原片失败（成品在{os.path.basename(dest)}）：{e}")
                    return (vp, dest, os.path.basename(os.path.dirname(dest)) + "|" + os.path.basename(music), backup)
                except Exception as e:
                    return (vp, f"FAILED:{e}", "")
                finally:
                    try:
                        if tmp and os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass
                    _bump()  # 进度实时走

            ex = ThreadPoolExecutor(max_workers=DUB_WORKERS)
            submitted_vps = set()
            try:
                for ci, vp in enumerate(targets, 1):
                    if self._dub_cancel.is_set():
                        cancelled = True
                        break
                    base = os.path.basename(vp)
                    music = _offline_music()
                    if not music:
                        rows.append((vp, "FAILED:无可用音乐", ""))
                        fail += 1
                        _bump()
                        continue
                    ext = os.path.splitext(vp)[1]
                    inplace = not do_rename
                    if do_rename:
                        nm = _offline_name() or base
                        newbase = safe_filename(nm)
                        dest = unique_dest_seen(os.path.join(os.path.dirname(vp), newbase + ext), used_dests)
                        used_dests.add(os.path.normcase(dest))
                    else:
                        dest = vp
                    backup = unique_dest_seen(os.path.join(backup_dir, base), used_backups)
                    used_backups.add(os.path.normcase(backup))
                    t = {"vp": vp, "music": music, "start": random.uniform(0, 30),
                         "dest": dest, "backup": backup, "inplace": inplace}
                    futs[ex.submit(_work, t)] = t
                    try:
                        submitted_vps.add(os.path.normcase(os.path.normpath(vp)))
                    except Exception:
                        pass
                    if len(futs) == 1:
                        ui(f"转码已开始（{DUB_WORKERS}路并发，可点取消）...")
                # 规划结束（或被取消），统一收转码结果
                if futs:
                    stop_submit = False
                    for fut in as_completed(dict(futs)):
                        if self._dub_cancel.is_set() and not stop_submit:
                            stop_submit = True
                            cancelled = True
                            ex.shutdown(wait=False, cancel_futures=True)
                            ui("取消中：等正在转码的收尾…")
                        t = futs[fut]
                        try:
                            row = fut.result()
                        except CancelledError:
                            row = (t["vp"], "CANCELLED:用户取消", "")
                        except Exception as e:
                            row = (t["vp"], f"FAILED:{e}", "")
                        rows.append(row)
                        if len(row) > 2 and not str(row[1]).startswith(("FAILED", "CANCELLED")):
                            ok += 1
                            ui(f"{ok + fail}. {os.path.basename(row[0])} -> {os.path.basename(row[1])} | {os.path.basename(row[2].split('|')[-1]) if '|' in str(row[2]) else ''}")
                        elif str(row[1]).startswith("CANCELLED"):
                            cancelled_n += 1
                            _bump()  # 被取消的任务没进 _work，这里补进度
                        else:
                            fail += 1
                            dbg.append(f"--- {row[0]}\n{row[1]}")
                            ui(f"失败 {os.path.basename(row[0])}: {str(row[1])[:60]}")
            finally:
                try:
                    ex.shutdown(wait=True)
                except Exception:
                    pass
            if cancelled:
                # 规划被取消：已提交的上面已收，剩下没提交的记取消。
                # 用 submitted 集合判定（不用 targets[len(futs):]，规划失败跳过提交时会错位多算/漏算）。
                accounted = set(submitted_vps)
                try:
                    for _vp, _st, *_ in rows:
                        try:
                            accounted.add(os.path.normcase(os.path.normpath(_vp)))
                        except Exception:
                            continue
                except Exception:
                    pass
                for vp in targets:
                    try:
                        k = os.path.normcase(os.path.normpath(vp))
                    except Exception:
                        continue
                    if k not in accounted:
                        rows.append((vp, "CANCELLED:用户取消", ""))
                        cancelled_n += 1
                        accounted.add(k)
                        _bump()
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            logp = unique_dest(os.path.join(out, f"distribute_log_dub_{ts}.csv"))
            log_err = None
            try:
                with open(logp, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["原视频", "新视频", "文件夹|音乐", "备份原片"])
                    w.writerows(rows)
            except Exception as e:
                log_err = e
                dbg.append(f"写日志失败: {e}")
            try:
                with open(os.path.join(out, f"dub_debug_{ts}.log"), "w", encoding="utf-8") as f:
                    f.write("\n".join(dbg))
            except Exception:
                pass
            # 完成提示也回主线程弹框，避免子线程 messagebox 卡死
            def _done():
                try:
                    self._dubbing = False
                except Exception:
                    pass
                try:
                    self.b_dub.config(state="normal")
                    self.b_dub_cancel.config(state="disabled")
                except Exception:
                    pass
                self._progress_done()
                try:
                    _fin = f"配乐完成：成功{ok}个，失败{fail}个" + (f"，取消{cancelled_n}个" if cancelled_n else "")
                    self.set_status(_fin)
                    if status_cb:
                        status_cb(_fin)
                except Exception:
                    pass
                try:
                    tail = f"\n取消{cancelled_n}个（已完成的保留，未开始的不动）" if cancelled_n else ""
                    if log_err is not None:
                        # 配乐日志写不进去 = 撤回链断 + 备份目录认不出，必须明说
                        messagebox.showwarning(
                            "警告",
                            f"配乐完成了，但日志写入失败：\n{logp}\n{log_err}\n\n"
                            f"没有这份日志，点“撤回上次”认不出哪些文件配过乐，"
                            f"备份目录 {os.path.basename(backup_dir)} 里的原片需要你手动拷回去。")
                    if ok > 0:
                        messagebox.showinfo("配乐完成", f"成功{ok}个，失败{fail}个{tail}\n日志：{os.path.basename(logp)}\n原片已备份在 {os.path.basename(backup_dir)}，点“撤回上次”可连配乐一起退；确认不撤可手动删备份省空间。")
                    else:
                        messagebox.showinfo("配乐完成", f"成功{ok}个，失败{fail}个{tail}\n日志：{os.path.basename(logp)}")
                except Exception:
                    pass
                try:
                    os.startfile(out)
                except Exception:
                    pass
                try:
                    self.refresh_llm_status()
                except Exception:
                    pass
            self._ui(_done)

        def run():
            # 外壳：_run_body 里任何没预料到的异常都必须把界面恢复，
            # 否则 _dubbing 一直是 True，之后所有分发/配乐/撤回都被挡住，界面像死了。
            try:
                _run_body()
            except Exception as e:
                self._dub_failed(f"配乐过程出错：{e}")

        threading.Thread(target=run, daemon=True).start()

    def _dub_failed(self, msg):
        """配乐中途出内部错误：恢复所有按钮/标志，并明确告诉用户（不能静默）。"""
        def _do():
            try:
                self._dubbing = False
                self.b_dub.config(state="normal")
                self.b_dub_cancel.config(state="disabled")
            except Exception:
                pass
            self._progress_done()
            try:
                self.set_status(msg)
            except Exception:
                pass
            try:
                messagebox.showerror("配乐出错", f"{msg}\n\n已完成的部分保留了原片备份，"
                                                 "可点“撤回上次”回退。")
            except Exception:
                pass
        self._ui(_do)



    # ---------- 逻辑 ----------
    def allowed(self):
        return get_allowed_exts(self.media_type.get())

    def rename_word(self):
        return rename_word_for(self.media_type.get())

    def file_dialog_types(self):
        mt = self.media_type.get()
        if mt == "video":
            pat = " ".join("*" + e for e in sorted(VIDEO_EXTS))
            return [("视频", pat), ("全部", "*.*")]
        if mt == "image":
            pat = " ".join("*" + e for e in sorted(IMAGE_EXTS))
            return [("图片", pat), ("全部", "*.*")]
        pat = " ".join("*" + e for e in sorted(ALL_EXTS))
        return [("视频+图片", pat), ("全部", "*.*")]

    def set_status(self, s):
        # 配乐子线程也会调用：必须回到主线程更新，否则 Tk 随机卡死/崩溃
        try:
            if threading.current_thread() is not threading.main_thread():
                try:
                    self.root.after(0, lambda: self.status.config(text=s))
                    return
                except Exception:
                    return
        except Exception:
            pass
        try:
            self.status.config(text=s)
        except Exception:
            return
        try:
            self.root.update_idletasks()
        except Exception:
            pass
class SplitPage(tk.Frame):
    """主窗口里的「均分 / 自定义数量 → 分发到多个指定文件夹」页面。

    用法：
      · 左边【素材】：把放素材的文件夹**拖进来**（分散在不同盘/位置都行）。
        拖入只是“引用”路径，不会搬走/复制任何文件；里面所有素材会被展开列出，
        并显示每个来源文件夹的素材数和总素材数。
      · 右边【目标】：把要分发到的文件夹**拖进来**（也可以点【+添加文件夹】浏览选）。
      · 分配：🌊均分（素材数平均分给每个目标，余数给靠前的）或 ✏️自定义数量（逐个填）。
      · 每个目标文件夹内部完全沿用原分发逻辑：起始日期 / 每组数量 / 间隔天数
        → MM-DD未分发 文件夹 → 文件夹名+视频/图片+序号 改名 → 写日志可撤回。
      · 配乐（音乐库）与改名（命名库）沿用同一套设置，只作用在这些目标文件夹里。
    """

    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app = app
        self.sources = []       # [{"path": 素材来源文件夹, "n": 其中素材数}]
        self.manual = []        # 单独添加的素材文件
        self.videos = []        # sources + manual 展开后的工作列表
        self.targets = []       # [{"path": 目标文件夹, "count": 数量}]
        self.last_folders = []  # 上次执行出来的 MM-DD未分发 文件夹
        self.last_log = None
        self._executing = False
        self._mode = app.split_mode
        self._build()
        self.refresh_all()
        if HAS_DND:
            # 两个列表分别注册成拖放目标：拖到左边=素材来源，拖到右边=目标文件夹
            try:
                self.src_tree.drop_target_register(DND_FILES)
                self.src_tree.dnd_bind("<<Drop>>", self.on_drop_sources)
                self.tree.drop_target_register(DND_FILES)
                self.tree.dnd_bind("<<Drop>>", self.on_drop_targets)
            except Exception:
                pass
        self.after(600, self._poll_dub)

    def on_shown(self):
        """切到本页时：刷新素材/目标列表，并按当前窗口高度重排表格区
        （页面隐藏时收不到 <Configure>，所以切回来必须再算一次）。"""
        try:
            self.refresh_all()
            self.refresh_lib_status()
        except Exception:
            pass
            pass

    # ---------- 界面 ----------
    def _build(self):
        sub = tk.Label(self, text="🧮 素材文件夹拖左边（只引用不搬走）· 目标文件夹拖右边 · 均分或自定义数量",
                       bg=BG, fg=SEA_DEEP, font=FONT_BOLD)
        sub.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))

        # 本页用 grid：表格区和日志区带权重（缩放时先被压缩），
        # 下面这些功能按钮行权重=0，窗口再小也一定显示得出来。
        # minsize 给可伸缩行一个下限，但**不能太大**，否则会把下面的按钮行顶出窗口。
        for _r, _wt, _ms in ((0, 0, 0), (1, 1, 120), (2, 0, 0), (3, 0, 0),
                             (4, 1, 40), (5, 0, 0), (6, 0, 0), (7, 0, 0)):
            self.rowconfigure(_r, weight=_wt, minsize=_ms)
        self.columnconfigure(0, weight=1)

        mid = tk.Frame(self, bg=BG)
        mid.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)

        # ================= 左：素材 =================
        left = tk.LabelFrame(mid, text="🐚 素材（把文件夹拖到这里，只引用不搬走）", bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        # 左栏比右栏多一张表（来源表 + 展开后的文件列表），必须用 grid：
        # pack 是"前面的先把空间吃光"，来源表一旦 expand，下面那行的
        # 【打开/移除选中来源】按钮会被压成 1px、文件列表直接被挤出面板（看着像一条空缝）。
        # 改成 grid 后两张表各占一行、都带权重，剩余空间平分，任何高度下都露得出来。
        left.columnconfigure(0, weight=1)
        # 左栏固定行(按钮+来源行+说明)约 95px，剩下的分给两张表。
        # 表格区权重给大一点：来源表是主信息，至少要露得出表头 + 1 行
        # （表头 24px + 行高 24px），否则行会被裁掉，看着像"加了却没显示"。
        for _r, _wt, _ms in ((0, 0, 0), (1, 0, 0), (2, 1, 62), (3, 0, 0), (4, 1, 46)):
            left.rowconfigure(_r, weight=_wt, minsize=_ms)

        lbtn = tk.Frame(left, bg=PANEL)
        lbtn.grid(row=0, column=0, sticky="ew")
        neon_button(tk.Button(lbtn, text="+素材文件夹", command=self.pick_sources), SEA_DEEP).pack(side="left", padx=2, pady=2)
        neon_button(tk.Button(lbtn, text="+文件", command=self.add_files), SEA_DEEP).pack(side="left", padx=2)
        neon_button(tk.Button(lbtn, text="清空素材", command=self.clear_videos), CORAL).pack(side="left", padx=2)
        # 素材总数：拖进来后一眼看到
        self.lbl_count = tk.Label(lbtn, text="素材 0 个", bg=PANEL, fg=CORAL, font=FONT_TITLE)
        self.lbl_count.pack(side="right", padx=6)

        sf = tk.Frame(left, bg=PANEL)
        sf.grid(row=1, column=0, sticky="ew")
        tk.Label(sf, text="素材来源：", bg=PANEL, fg=MUTED, font=FONT_MAIN).pack(side="left", padx=2)
        self.lbl_src_total = tk.Label(sf, text="0 个", bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        self.lbl_src_total.pack(side="left", padx=4)
        # 原来这两个按钮单独占一行，左栏高度不够时会把两张表挤没，
        # 所以挪到"素材来源"这行右对齐，省下一整行给表格。
        neon_button(tk.Button(sf, text="打开", command=self.source_open), SEA_BLUE).pack(side="right", padx=2)
        neon_button(tk.Button(sf, text="移除选中", command=self.source_del), CORAL).pack(side="right", padx=2)

        sframe = tk.Frame(left, bg=PANEL)
        sframe.grid(row=2, column=0, sticky="nsew", pady=(2, 0))
        sframe.rowconfigure(0, weight=1)
        sframe.columnconfigure(0, weight=1)
        self.src_tree = ttk.Treeview(sframe, columns=("path", "n"), show="headings", height=3)
        self.src_tree.heading("path", text="素材文件夹")
        self.src_tree.heading("n", text="素材数")
        self.src_tree.column("path", width=150, minwidth=80, stretch=True)
        self.src_tree.column("n", width=60, anchor="center")
        self.src_tree.grid(row=0, column=0, sticky="nsew")
        # 横向滚动条绑 xview/xscrollcommand（原来错绑成 yview，横向根本拖不动）
        ssb = tk.Scrollbar(sframe, command=self.src_tree.xview, orient="horizontal")
        self.src_tree.configure(xscrollcommand=ssb.set)
        ssb.grid(row=1, column=0, sticky="ew")
        self.src_tree.bind("<Double-1>", self.source_open)

        tk.Label(left, text="展开后的素材文件：", bg=PANEL, fg=MUTED, font=FONT_MAIN,
                 anchor="w").grid(row=3, column=0, sticky="ew", padx=2)
        lb_frame = tk.Frame(left, bg=PANEL)
        lb_frame.grid(row=4, column=0, sticky="nsew")
        lb_frame.rowconfigure(0, weight=1)
        lb_frame.columnconfigure(0, weight=1)
        self.listbox = tk.Listbox(lb_frame, selectmode="extended", height=2, bg="#FFFFFF", fg=TEXT,
                                  selectbackground="#BFE6EC", selectforeground=SEA_DEEP,
                                  relief="groove", highlightbackground="#BFE6EC", highlightthickness=1)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        sb1 = tk.Scrollbar(lb_frame, command=self.listbox.yview)
        sb1.grid(row=0, column=1, sticky="ns")
        self.listbox.config(yscrollcommand=sb1.set)

        # ================= 右：目标 =================
        right = tk.LabelFrame(mid, text="⛵ 目标文件夹（把文件夹拖到这里）+ 每个放几个", bg=PANEL, fg=CORAL, font=FONT_BOLD)
        right.pack(side="left", fill="both", expand=True, padx=(4, 0))

        mode_row = tk.Frame(right, bg=PANEL)
        mode_row.pack(fill="x", pady=(2, 0))
        tk.Radiobutton(mode_row, text="🌊 均分", value="even", variable=self._mode,
                       bg=PANEL, fg=SEA_DEEP, selectcolor="#FFFFFF", activebackground=PANEL,
                       activeforeground=SEA_DEEP, font=FONT_BOLD,
                       command=self.on_mode).pack(side="left", padx=(4, 2))
        tk.Radiobutton(mode_row, text="✏️ 自定义数量", value="custom", variable=self._mode,
                       bg=PANEL, fg=SEA_DEEP, selectcolor="#FFFFFF", activebackground=PANEL,
                       activeforeground=SEA_DEEP, font=FONT_BOLD,
                       command=self.on_mode).pack(side="left", padx=8)
        neon_button(tk.Button(mode_row, text="重新均分", command=self.gen_even), SEA_DEEP).pack(side="left", padx=4)

        tbtn = tk.Frame(right, bg=PANEL)
        tbtn.pack(fill="x", pady=(2, 0))
        neon_button(tk.Button(tbtn, text="+添加文件夹", command=self.add_target), SEA_DEEP).pack(side="left", padx=2, pady=2)
        neon_button(tk.Button(tbtn, text="-删选中", command=self.target_del), CORAL).pack(side="left", padx=2)
        neon_button(tk.Button(tbtn, text="打开", command=self.target_open), SEA_BLUE).pack(side="left", padx=2)
        self.lbl_plan = tk.Label(tbtn, text="", bg=PANEL, fg=CORAL, font=FONT_BOLD)
        self.lbl_plan.pack(side="right", padx=4)

        tframe = tk.Frame(right, bg=PANEL)
        tframe.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tframe, columns=("path", "count"), show="headings", height=8)
        self.tree.heading("path", text="目标文件夹（分发到这个位置）")
        self.tree.heading("count", text="放几个")
        self.tree.column("path", width=170, minwidth=80, stretch=True)
        self.tree.column("count", width=70, anchor="center")
        self.tree.pack(fill="both", expand=True, side="left")
        sb2 = tk.Scrollbar(tframe, command=self.tree.yview)
        sb2.pack(fill="y", side="right")
        self.tree.config(yscrollcommand=sb2.set)
        self.tree.bind("<Double-1>", self.target_edit)

        # ---- 目标内部规则（与主界面共用变量）----
        rules = tk.LabelFrame(self, text="📅 每个目标文件夹内部 · 沿用原分发规则（和“按日期分发”页共用同一组数值）")
        rules.grid(row=2, column=0, sticky="ew", padx=8, pady=2)
        rules.configure(bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        r1 = tk.Frame(rules, bg=PANEL)
        r1.pack(fill="x")
        # 拆成两行：单行太长（≈890px）会把整页最小宽度撑爆，窗口缩窄时右侧被切掉。
        # 两行都允许被压缩，页面才能跟着窗口一起变窄。
        ra = tk.Frame(r1, bg=PANEL)
        ra.pack(fill="x")
        rb = tk.Frame(r1, bg=PANEL)
        rb.pack(fill="x")
        tk.Label(ra, text="从", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        tk.Entry(ra, textvariable=self.app.start_date_var, width=11, bg="#FFFFFF", fg=TEXT,
                 insertbackground=SEA_DEEP).pack(side="left")
        tk.Label(ra, text="开始 每组", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        tk.Entry(ra, textvariable=self.app.per_group, width=5, bg="#FFFFFF", fg=TEXT).pack(side="left")
        tk.Label(ra, text="个 间隔", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        tk.Entry(ra, textvariable=self.app.interval, width=5, bg="#FFFFFF", fg=TEXT).pack(side="left")
        tk.Label(ra, text="天(0=连续) → 生成 MM-DD未分发", bg=PANEL, fg=TEXT).pack(side="left", padx=2)
        tk.Checkbutton(rb, text="改名（文件夹名+视频/图片+序号）", variable=self.app.do_rename,
                       bg=PANEL, fg=CORAL, selectcolor="#FFFFFF",
                       command=self.app.persist).pack(side="left", padx=2)
        self.lbl_type = tk.Label(rb, text="", bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        self.lbl_type.pack(side="left", padx=6)

        ctl = tk.Frame(self, bg=BG)
        ctl.grid(row=3, column=0, sticky="ew", padx=8, pady=2)
        neon_button(tk.Button(ctl, text="🌊 预览分配", command=self.preview), SEA_DEEP).pack(side="left", padx=4)
        self.b_exec = tk.Button(ctl, text="⛵ 确认执行（移动到这些文件夹）", command=self.execute)
        self.b_exec.configure(bg=CORAL, fg="#FFFFFF", activebackground=SEA_DEEP, activeforeground="#FFFFFF",
                              relief="groove", font=FONT_BOLD, cursor="hand2", padx=12, pady=4)
        self.b_exec.pack(side="left", padx=4)
        self.b_undo = tk.Button(ctl, text="🐚 撤回上次这次分发", command=self.on_undo_last)
        neon_button(self.b_undo, SEA_BLUE).pack(side="left", padx=4)
        tk.Label(ctl, text="执行后可用下方配乐/改名", bg=BG, fg=MUTED, font=FONT_MAIN).pack(side="left", padx=8)

        self.log_text = tk.Text(self, height=4, bg="#FFFFFF", fg=TEXT, insertbackground=SEA_DEEP,
                                relief="groove", highlightbackground="#BFE6EC", highlightthickness=1)
        self.log_text.grid(row=4, column=0, sticky="nsew", padx=8, pady=(2, 2))

        act = tk.LabelFrame(self, text="🎵 只处理上面这些目标文件夹 · 音乐库/命名库见下方共用栏 🎵")
        act.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 4))
        act.configure(bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        a1 = tk.Frame(act, bg=PANEL)
        a1.pack(fill="x")
        self.b_dub = big_button(tk.Button(a1, text="🎧 一键配乐", command=self.on_dub), SEA_DEEP)
        self.b_dub.pack(side="left", padx=6, pady=2)
        self.b_rename = big_button(tk.Button(a1, text="✏️ 只按命名库改名", command=self.on_rename_only),
                                   SEA_BLUE, primary=False)
        self.b_rename.pack(side="left", padx=4, pady=2)
        self.lbl_lib = tk.Label(a1, text="", bg=PANEL, fg=SEA_DEEP, font=FONT_BOLD)
        self.lbl_lib.pack(side="left", padx=8)

        self.status = tk.Label(self, text="🌊 就绪：拖素材文件夹到左边 → 拖目标文件夹到右边 → 预览 → 执行",
                               anchor="w", relief="groove", bg="#FFFFFF", fg=SEA_DEEP, font=FONT_MAIN)
        self.status.grid(row=6, column=0, sticky="ew")
        self.progress = ttk.Progressbar(self, mode="determinate")
        # 关键修复：窗口变矮时页面要跟着收缩，否则底部功能按钮会被顶到窗口外面看不见。
        # 做法：把"素材/目标表格区"的请求高度固定住（不再随内容长高），
        # 它和日志区用 grid 权重吸收剩余空间，固定行（规则/执行/配乐/状态）就永远留在窗口内。
        self._mid = mid
        self._log_text = self.log_text
        self._setup_flexible()

    # ---------- 小工具 ----------
    def _setup_flexible(self):
        """让页面能真正随窗口收缩，底部功能按钮永远不被挤出窗口。

        为什么固定高度、不做"按窗口动态拉伸"：
        Tk 的 grid 里控件不会小于自己的请求高度，而"运行时算完再拉伸"会
        出现拉大后缩不回来的死结（小窗口时又被顶出去）。所以这里老老实实
        把表格区固定成 170px（内部是可滚动的 Treeview/Listbox，够用），
        剩下的空间由 grid 权重分给日志区，固定行始终留在窗口内。
        """
        try:
            mid = self._mid
            # mid 的子控件用 pack 放置，必须 pack_propagate(False) 才能压住请求高度
            # （用成 grid_propagate 不会报错，但完全不起作用）。
            mid.configure(height=120)
            mid.pack_propagate(False)
            self.log_text.configure(height=2)
            # 表格区最多占 120px，日志区最多占 40px；剩下的都给上面固定行
            # （规则/执行/配乐/状态），这样页面最小需求高度 ≈ 350px，
            # 在 800x560 的窗口里也能完整放下，不会盖住下面的音乐库栏。
            self.rowconfigure(1, weight=1, minsize=120)
            self.rowconfigure(4, weight=1, minsize=34)
        except Exception:
            pass


    # ---------- 小工具 ----------
    def allowed(self):
        return get_allowed_exts(self.app.media_type.get())

    def file_dialog_types(self):
        return self.app.file_dialog_types()

    def set_status(self, s):
        """状态栏在共用栏，本页统一走 App 的实现（配乐子线程也安全）。"""
        try:
            self.app.set_status(s)
        except Exception:
            pass

    def _ui(self, fn):
        """子线程回主线程执行 UI 操作。"""
        try:
            self.app.root.after(0, fn)
        except Exception:
            pass

    def append_log(self, s):
        try:
            self.log_text.insert(tk.END, s if s.endswith("\n") else s + "\n")
            self.log_text.see(tk.END)
        except Exception:
            pass

    def clear_log(self):
        try:
            self.log_text.delete("1.0", tk.END)
        except Exception:
            pass

    def _drop_paths(self, event):
        """把拖放事件里的路径解析成列表（Windows 用 {} 包裹，含中文/空格）。"""
        out = []
        try:
            raw = event.data
        except Exception:
            return out
        if not raw:
            return out
        raw = raw.strip()
        if raw.startswith("{") and raw.endswith("}"):
            out.extend([p for p in raw[1:-1].split("} {") if p])
        else:
            out.extend([p for p in raw.split() if p])
        return [os.path.abspath(p) for p in out if p]

    def _progress_start(self, total):
        try:
            self.progress.config(maximum=max(1, int(total)), value=0)
        except Exception:
            pass

    def _progress_set(self, done, total=None):
        try:
            self.progress.config(value=int(done))
        except Exception:
            pass

    def _progress_done(self):
        try:
            self.progress.config(value=0)
        except Exception:
            pass

    # ---------- 素材来源 ----------
    def rebuild_videos(self):
        """按来源文件夹 + 单独添加的文件，重建工作列表（只引用路径，不复制文件）。"""
        uniq = {}
        for s in self.sources:
            try:
                for f in collect_files([s["path"]], self.allowed()):
                    uniq.setdefault(_pk(f), f)
            except Exception:
                pass
        for f in self.manual:
            uniq.setdefault(_pk(f), f)
        self.videos = sort_files(list(uniq.values()))
        if self.app.shuffle.get():
            random.shuffle(self.videos)

    def refresh_all(self):
        self.refresh_sources()
        self.refresh_video_list()
        self.refresh_targets()

    def refresh_sources(self):
        try:
            for i in self.src_tree.get_children():
                self.src_tree.delete(i)
            for s in self.sources:
                self.src_tree.insert("", tk.END, values=(s["path"], s["n"]))
            self.src_tree.insert("", tk.END, values=(f"（单独添加的文件 {len(self.manual)} 个）", len(self.manual)))
            self.lbl_src_total.config(text=f"{len(self.sources)} 个来源文件夹")
        except Exception:
            pass

    def refresh_video_list(self):
        try:
            self.listbox.delete(0, tk.END)
            for i, v in enumerate(self.videos, 1):
                parent = os.path.basename(os.path.dirname(v))
                self.listbox.insert(tk.END, f"{i}. {parent}/{os.path.basename(v)}")
            self.lbl_count.config(text=f"素材 {len(self.videos)} 个")
            mt = self.app.media_type.get()
            word = {"video": "🎬 视频", "image": "📷 图片"}.get(mt, "🌊 视频+图片")
            self.lbl_type.config(text=f"分发类型：{word}")
        except Exception:
            pass

    def add_source_paths(self, paths, quiet=False):
        """拖进来/选进来的素材路径：文件夹记为来源（只引用），文件直接进列表。"""
        added_dirs = added_files = 0
        for p in paths:
            try:
                if os.path.isdir(p):
                    if any(_pk(s["path"]) == _pk(p) for s in self.sources):
                        continue
                    n = len(collect_files([p], self.allowed()))
                    self.sources.append({"path": p, "n": n})
                    added_dirs += 1
                elif os.path.isfile(p):
                    if os.path.splitext(p)[1].lower() in {e.lower() for e in self.allowed()}:
                        if _pk(p) not in {_pk(x) for x in self.manual}:
                            self.manual.append(p)
                            added_files += 1
            except Exception:
                continue
        if not added_dirs and not added_files:
            if not quiet:
                messagebox.showinfo("提示", "没有新的素材（可能已经加过了，或没有对应类型的文件）",
                                    parent=self.winfo_toplevel())
            return
        self.rebuild_videos()
        self.refresh_all()
        if self._mode == "even" and self.videos:
            self.gen_even(silent=True)
        else:
            self.refresh_targets()
        self.set_status(f"素材 {len(self.videos)} 个（新增文件夹{added_dirs}个/文件{added_files}个）")
        self.app.persist()

    def pick_sources(self):
        d = filedialog.askdirectory(title="选择素材文件夹（只引用，不会搬走）", parent=self.winfo_toplevel())
        if d:
            self.add_source_paths([os.path.abspath(d)])

    def source_del(self):
        sel = self.src_tree.selection()
        if not sel:
            return
        idxs = sorted([self.src_tree.index(s) for s in sel], reverse=True)
        for i in idxs:
            if 0 <= i < len(self.sources):
                del self.sources[i]
        self.rebuild_videos()
        self.refresh_all()
        if self._mode == "even" and self.videos:
            self.gen_even(silent=True)
        self.set_status(f"已移除来源，素材 {len(self.videos)} 个")

    def source_open(self, _event=None):
        sel = self.src_tree.selection()
        if not sel:
            return
        for s in sel:
            i = self.src_tree.index(s)
            if 0 <= i < len(self.sources):
                try:
                    os.startfile(self.sources[i]["path"])
                except Exception:
                    pass

    def add_files(self):
        fs = filedialog.askopenfilenames(title="选择素材文件", filetypes=self.file_dialog_types(),
                                         parent=self.winfo_toplevel())
        if fs:
            self.add_source_paths([os.path.abspath(f) for f in fs])

    def add_folder(self):
        d = filedialog.askdirectory(title="选择素材文件夹（含子文件夹）", parent=self.winfo_toplevel())
        if d:
            self.add_source_paths([os.path.abspath(d)])

    def clear_videos(self):
        self.sources = []
        self.manual = []
        self.videos = []
        self.refresh_all()
        self.clear_log()
        self.append_log("已清空素材，请重新拖入。")
        self.set_status("已清空素材")

    def on_drop(self, event):
        """拖到本页空白处：按素材来源处理（根窗口的全局拖放会转到这里）。"""
        self.on_drop_sources(event)

    def on_drop_sources(self, event):
        now = int(time.time() * 1000)
        if now - getattr(self.app, "_last_drop_ms", 0) < 300:
            return
        self.app._last_drop_ms = now
        paths = self._drop_paths(event)
        if paths:
            self.add_source_paths(paths)

    def on_drop_targets(self, event):
        now = int(time.time() * 1000)
        if now - getattr(self.app, "_last_drop_ms", 0) < 300:
            return
        self.app._last_drop_ms = now
        paths = [p for p in self._drop_paths(event) if os.path.isdir(p)]
        if not paths:
            messagebox.showinfo("提示", "目标位置请拖文件夹进来（文件不行）", parent=self.winfo_toplevel())
            return
        self.add_targets(paths)

    # ---------- 目标文件夹 ----------
    def refresh_targets(self):
        try:
            for i in self.tree.get_children():
                self.tree.delete(i)
            for t in self.targets:
                self.tree.insert("", tk.END, values=(t["path"], t["count"]))
            total_plan = sum(t["count"] for t in self.targets)
            self.lbl_plan.config(text=f"目标{len(self.targets)}个 · 计划{total_plan}个 / 素材{len(self.videos)}待分")
        except Exception:
            pass

    def on_mode(self):
        self.app.split_mode.set(self._mode)
        self.app.persist()
        if self._mode == "even":
            self.gen_even(silent=True)
            self.set_status("已切到均分：素材数平均分给每个目标文件夹")
        else:
            self.set_status("已切到自定义数量：双击表格改“放几个”，或删掉不用的目标")

    def add_targets(self, paths):
        added = 0
        for d in paths:
            d = os.path.abspath(d)
            if any(_pk(t["path"]) == _pk(d) for t in self.targets):
                continue
            try:
                os.makedirs(d, exist_ok=True)
            except Exception as e:
                messagebox.showwarning("提示", f"用不了这个目标位置：\n{d}\n{e}", parent=self.winfo_toplevel())
                continue
            self.targets.append({"path": d, "count": 0})
            added += 1
        # 先给一份"基准均分"：不管当前是均分还是自定义模式，新加的目标都按
        # 素材数平均铺一遍，避免 11个素材/3个目标 变成 3/3/3 剩下2个没地方放
        if self.videos:
            self.gen_even(silent=True)
        else:
            for t in self.targets:
                if t["count"] == 0:
                    t["count"] = 1
            self.refresh_targets()
        if added:
            self.set_status(f"已添加{added}个目标文件夹，当前{len(self.targets)}个")
        self.app.persist()

    def add_target(self):
        d = filedialog.askdirectory(title="选择目标文件夹（分发到这个位置）", parent=self.winfo_toplevel())
        if d:
            self.add_targets([os.path.abspath(d)])

    def _suggest_count(self, n_targets):
        total = len(self.videos)
        if not total:
            return 1
        return max(1, total // max(1, n_targets))

    def target_del(self):
        sel = self.tree.selection()
        if not sel:
            return
        idxs = sorted([self.tree.index(s) for s in sel], reverse=True)
        for i in idxs:
            if 0 <= i < len(self.targets):
                del self.targets[i]
        self.refresh_targets()

    def target_open(self, _event=None):
        sel = self.tree.selection()
        paths = [self.targets[self.tree.index(s)]["path"] for s in sel] if sel else \
            [t["path"] for t in self.targets]
        if not paths:
            messagebox.showinfo("提示", "先拖入或添加目标文件夹", parent=self.winfo_toplevel())
            return
        for p in paths[:3]:
            try:
                if os.path.isdir(p):
                    os.startfile(p)
            except Exception:
                pass

    def target_edit(self, _event=None):
        from tkinter import simpledialog
        sel = self.tree.selection()
        if not sel:
            return
        i = self.tree.index(sel[0])
        if not (0 <= i < len(self.targets)):
            return
        t = self.targets[i]
        new_p = simpledialog.askstring("改目标文件夹", "目标文件夹位置（可直接粘贴路径）",
                                        initialvalue=t["path"], parent=self.winfo_toplevel())
        if new_p:
            p = os.path.abspath(new_p.strip().strip('"'))
            try:
                os.makedirs(p, exist_ok=True)
            except Exception as e:
                messagebox.showwarning("提示", f"用不了这个路径：\n{p}\n{e}", parent=self.winfo_toplevel())
            else:
                t["path"] = p
        new_c = simpledialog.askstring("改数量", "这个文件夹放几个", initialvalue=str(t["count"]),
                                       parent=self.winfo_toplevel())
        if new_c:
            try:
                c = int(str(new_c).strip())
                if c < 0:
                    raise ValueError
                t["count"] = c
            except Exception:
                messagebox.showwarning("提示", "数量必须是不小于0的整数", parent=self.winfo_toplevel())
        self.refresh_targets()
        self.app.persist()

    def gen_even(self, silent=False):
        """把素材数平均分给所有目标文件夹（余数给靠前的几个）。"""
        n = len(self.targets)
        if not n:
            if not silent:
                messagebox.showinfo("提示", "先拖入或添加要分发到的目标文件夹", parent=self.winfo_toplevel())
            return
        total = len(self.videos)
        if total <= 0:
            if not silent:
                messagebox.showinfo("提示", "先拖入素材", parent=self.winfo_toplevel())
            return
        warn = ""
        if total < n:
            warn = f"素材只有{total}个 < 目标{n}个，后面的目标这次不会有文件"
            counts = [1] * total + [0] * (n - total)
        else:
            base, rem = divmod(total, n)
            counts = [base + (1 if k < rem else 0) for k in range(n)]
        for t, c in zip(self.targets, counts):
            t["count"] = c
        self.refresh_targets()
        self.app.persist()
        if not silent:
            self.set_status(f"已均分：素材{total}个 → {n}个目标文件夹{'，' + warn if warn else ''}")
            self.preview()

    # ---------- 分配 ----------
    def _clean_targets(self):
        out = []
        seen = set()
        for t in self.targets:
            p = str(t.get("path") or "").strip().strip('"')
            if not p:
                continue
            try:
                c = int(t.get("count"))
            except Exception:
                c = 0
            if c <= 0:
                continue
            p = os.path.abspath(p)
            k = _pk(p)
            if k in seen:
                continue
            seen.add(k)
            out.append({"path": p, "count": c})
        return out

    def _date_rules(self):
        """目标文件夹内部用的原分发规则：起始日期 / 每组数量 / 间隔天数。"""
        s = self.app.start_date_var.get()
        if not is_valid_date_str(s):
            raise ValueError("起始日期格式不对，用 2026-09-08 或 09-08")
        d = parse_start_date(s)
        per = max(1, safe_int_var(self.app.per_group, 15))
        inv = max(0, min(MAX_INTERVAL_DAYS, safe_int_var(self.app.interval, 0)))
        return d, per, inv

    def build_plan_for_targets(self):
        """两级分配：素材 → 指定目标文件夹（均分/自定义）→ 各目标内部按原日期规则切分。

        返回 (job, notes)，job = [{"root": 目标文件夹, "files": [...],
                                  "groups": [(date, MM-DD未分发, [文件...]), ...]}]
        """
        notes = []
        files = list(self.videos)
        targets = self._clean_targets()
        if not files:
            return [], ["先拖入素材（左边）。"]
        if not targets:
            return [], ["先拖入或添加目标文件夹（右边）。"]
        try:
            start, per, inv = self._date_rules()
        except Exception as e:
            return [], [str(e)]
        idx = 0
        job = []
        for t in targets:
            chunk = files[idx:idx + t["count"]]
            if not chunk:
                notes.append(f"⚠{t['path']} 分到 0 个（数量是0或素材不够）")
                continue
            idx += len(chunk)
            plan = build_plan(len(chunk), per, start, inv)
            groups = allocate(chunk, plan)
            allocs, appended = ensure_tail_group(chunk, groups, plan, lambda: start, inv)
            if appended:
                notes.append(f"⚠{t['path']} 计划不够，剩 {len(allocs[-1][2])} 个并进了 {allocs[-1][1]}")
            job.append({"root": t["path"], "files": chunk, "groups": list(allocs)})
        if idx < len(files):
            rest = len(files) - idx
            notes.append(f"⚠目标总数量合计少于素材，多出的 {rest} 个没地方放："
                         f"调大某个目标的数量，或再加一个目标文件夹")
        return job, notes

    def preview(self):
        try:
            self.log_text.delete("1.0", tk.END)
        except Exception:
            pass
        job, notes = self.build_plan_for_targets()
        for n in notes:
            self.log_text.insert(tk.END, n + "\n")
        if not job:
            if not notes:
                self.log_text.insert(tk.END, "先拖入素材和目标文件夹后预览。\n")
            self.set_status("无法预览")
            return
        mode = "🌊 打乱分发" if self.app.shuffle.get() else "⛵ 顺序分发"
        self.log_text.insert(tk.END, f"素材{len(self.videos)}个 · {mode} → {len(job)}个目标文件夹\n")
        rw = "auto" if self.app.media_type.get() == "all" else self.app.rename_word()
        total_groups = 0
        for j in job:
            total_groups += len(j["groups"])
            self.log_text.insert(tk.END, f"\n📁 目标：{j['root']}   共{len(j['files'])}个 / {len(j['groups'])}个日期文件夹\n")
            for dt, fname, fs in j["groups"]:
                self.log_text.insert(tk.END, f"   【{fname}】{dt} {len(fs)}个\n")
                for idx, f in enumerate(fs[:3], 1):
                    parent = os.path.basename(os.path.dirname(f))
                    if self.app.do_rename.get():
                        ext = os.path.splitext(f)[1]
                        w = _rename_word_for_file(rw, f)
                        self.log_text.insert(tk.END,
                                             f"      - {parent}/{os.path.basename(f)} -> {fname}{w}{idx}{ext}\n")
                    else:
                        self.log_text.insert(tk.END, f"      - {parent}/{os.path.basename(f)}\n")
                if len(fs) > 3:
                    self.log_text.insert(tk.END, f"      ...还有{len(fs)-3}个\n")
        self.set_status(f"预览：素材{len(self.videos)}个 → {len(job)}个目标 / {total_groups}个日期文件夹")

    # ---------- 执行 ----------
    def execute(self):
        if self.app.busy("分发"):
            return
        job, notes = self.build_plan_for_targets()
        if not job:
            messagebox.showwarning("提示", "\n".join(notes) or "没有可分发的内容", parent=self.winfo_toplevel())
            return
        summary = "\n".join([f"{j['root']}：{len(j['files'])}个 → "
                             + "、".join(f"{fn}({len(fs)})" for _dt, fn, fs in j["groups"])
                             for j in job])
        rw = "auto" if self.app.media_type.get() == "all" else self.app.rename_word()
        rn = "，并改名为 文件夹名+视频/图片+序号" if self.app.do_rename.get() else ""
        if not messagebox.askyesno(
                "确认执行",
                f"将移动{len(self.videos)}个素材到这{len(job)}个指定文件夹：\n\n{summary}\n\n"
                f"每个目标内部按 {self.app.start_date_var.get()} 起的日期规则生成 MM-DD未分发{rn}，确定吗？",
                parent=self.winfo_toplevel()):
            return
        self._executing = True
        self.app._executing = True
        try:
            self.b_exec.config(state="disabled")
        except Exception:
            pass
        self.set_status("执行中...")
        self.app.persist()
        total = sum(len(j["files"]) for j in job)
        self._progress_start(total)
        do_rename = self.app.do_rename.get()   # Tk 变量必须在主线程读完

        def _body():
            rows = []
            moved = 0
            made_folders = []
            for j in job:
                try:
                    os.makedirs(j["root"], exist_ok=True)
                except Exception as e:
                    for f in j["files"]:
                        rows.append((f, f"FAILED:目标文件夹建不了:{e}", os.path.basename(j["root"])))
                    continue
                _m, _rows = execute_move(list(j["groups"]), j["root"],
                                         rename=do_rename, rename_word=rw,
                                         progress=make_throttled_progress(self._progress_set))
                moved += _m
                rows.extend(_rows)
                for _dt, fn, _fs in j["groups"]:
                    p = os.path.join(j["root"], fn)
                    if os.path.isdir(p):
                        made_folders.append(p)

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = self._log_dir(job)
            logp = unique_dest(os.path.join(log_dir, f"distribute_log_{ts}.csv"))
            log_err = None
            try:
                os.makedirs(log_dir, exist_ok=True)
                with open(logp, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["原路径", "新路径", "日期文件夹"])
                    w.writerows(rows)
            except Exception as e:
                log_err = e

            def _done():
                self._executing = False
                self.app._executing = False
                self._progress_done()
                try:
                    self.b_exec.config(state="normal")
                except Exception:
                    pass
                moved_srcs = {r[0] for r in rows if not str(r[1]).startswith("FAILED")
                              and not str(r[1]).startswith("SKIPPED:源文件不存在")}
                for s in self.sources:
                    s["n"] = len(collect_files([s["path"]], self.allowed()))
                self.manual = [f for f in self.manual if _pk(f) not in {_pk(x) for x in moved_srcs}]
                self.rebuild_videos()
                self.last_folders = made_folders
                self.last_log = logp
                self.refresh_all()
                skipped = sum(1 for r in rows if str(r[1]).startswith("SKIPPED"))
                failed = sum(1 for r in rows if str(r[1]).startswith("FAILED"))
                msg = (f"完成：移动{moved}个 → {len(job)}个目标位置 / {len(self.last_folders)}个日期文件夹，"
                       f"跳过{skipped}个，失败{failed}个，素材剩{len(self.videos)}个")
                self.set_status(msg)
                self.append_log(f"\n{msg}\n日志：{os.path.basename(logp)}（在 {log_dir}）")
                if log_err is not None:
                    messagebox.showwarning("警告", f"分发完成了，但日志写入失败：\n{logp}\n{log_err}\n"
                                                  "本次分发将无法撤回，请检查磁盘空间/权限。",
                                           parent=self.winfo_toplevel())
                messagebox.showinfo(
                    "完成",
                    f"已移动{moved}个\n跳过{skipped}个，失败{failed}个\n"
                    f"目标位置 {len(job)} 个 → 生成 {len(self.last_folders)} 个日期文件夹\n"
                    f"日志：{os.path.basename(logp)}（{log_dir}）\n\n"
                    "可用下面的【一键配乐】【只按命名库改名】【撤回上次这次分发】处理它们。",
                    parent=self.winfo_toplevel())
                self.refresh_lib_status()
            self._ui(_done)

        def run():
            try:
                _body()
            except Exception as e:
                self._exec_failed("分发过程出错", e)

        threading.Thread(target=run, daemon=True).start()

    def _log_dir(self, job):
        """日志放哪：优先主界面的“分发位置”（那里就是撤回会去翻的地方），
        没有就用第一个目标文件夹的上一级。找不到可用目录时才用当前目录。"""
        cands = []
        try:
            out = self.app.output_root.get()
            if out:
                cands.append(out)
        except Exception:
            pass
        try:
            if job:
                cands.append(os.path.dirname(job[0]["root"]))
        except Exception:
            pass
        for d in cands:
            try:
                os.makedirs(d, exist_ok=True)
                if os.path.isdir(d):
                    return d
            except Exception:
                continue
        return os.getcwd()

    def _exec_failed(self, what, e):
        def _do():
            try:
                self._executing = False
                self.app._executing = False
                self.b_exec.config(state="normal")
            except Exception:
                pass
            self._progress_done()
            try:
                self.set_status(f"{what}：{e}")
            except Exception:
                pass
            try:
                messagebox.showerror(what, f"{e}", parent=self.winfo_toplevel())
            except Exception:
                pass
        self._ui(_do)

    # ---------- 撤回 ----------
    def on_undo_last(self):
        if self.app.busy("撤回"):
            return
        log_dir = None
        if self.last_log:
            log_dir = os.path.dirname(self.last_log)
        elif self.targets:
            try:
                log_dir = self._log_dir([{"path": self.targets[0]["path"]}])
            except Exception:
                log_dir = None
        if not log_dir or not os.path.isdir(log_dir):
            messagebox.showinfo("提示", "本页还没有执行过分发，先执行一次再撤回", parent=self.winfo_toplevel())
            return
        lp, n = latest_restorable_log(log_dir)
        if not lp or n == 0:
            messagebox.showinfo("提示", "最近的分发日志里没有可撤回的文件\n（文件可能已被手动删除或移走）",
                                parent=self.winfo_toplevel())
            return
        if not messagebox.askyesno("撤回确认",
                                   f"将撤回{n}个文件（退回原位置+原文件名，配乐/改名的一起退）：\n"
                                   f"{os.path.basename(lp)}\n所在目录：{log_dir}\n确定吗？",
                                   parent=self.winfo_toplevel()):
            return
        self.app._undoing = True
        self.set_status("撤回中...")

        def run():
            try:
                ok, fail, dubbed, from_backup, _marked, retry_log, renamed_back = undo_distribute(lp, log_dir)
            except Exception as e:
                def _fail():
                    self.app._undoing = False
                    self.set_status(f"撤回出错：{e}")
                    messagebox.showwarning("撤回出错", f"{e}", parent=self.winfo_toplevel())
                self._ui(_fail)
                return

            def _done():
                self.app._undoing = False
                for s in self.sources:
                    s["n"] = len(collect_files([s["path"]], self.allowed()))
                self.rebuild_videos()
                self.refresh_all()
                self.set_status(f"撤回完成：{ok}个，失败{fail}个，素材回到 {len(self.videos)} 个")
                msg = f"已撤回{ok}个，失败{fail}个（素材列表已刷新：{len(self.videos)}个）"
                if from_backup:
                    msg += f"\n其中{from_backup}个用配乐前原片恢复"
                if dubbed:
                    msg += f"\n其中{dubbed}个是配乐后版本（位置和文件名已恢复）"
                if renamed_back:
                    msg += (f"\n⚠{len(renamed_back)}个因原位置被占用而改了名："
                            + "、".join(renamed_back[:5]) + ("…" if len(renamed_back) > 5 else ""))
                if retry_log:
                    msg += f"\n失败的{fail}个已另存 {os.path.basename(retry_log)}，关掉占用它的程序后再点一次即可重试"
                self.append_log(f"\n{msg}")
                messagebox.showinfo("撤回完成", msg, parent=self.winfo_toplevel())
            self._ui(_done)
        threading.Thread(target=run, daemon=True).start()

    # ---------- 音乐库 / 命名库 ----------
    def target_dated_folders(self):
        """这些目标文件夹里现成的 *未分发 文件夹（配乐/命名库改名的对象）。"""
        out = []
        seen = set()
        for f in self.last_folders:
            if os.path.isdir(f) and _pk(f) not in seen:
                seen.add(_pk(f))
                out.append(f)
        for t in self._clean_targets():
            r = t["path"]
            try:
                names = sorted(os.listdir(r), key=natsort_key)
            except Exception:
                continue
            for n in names:
                if n.startswith(".") or n.startswith(DUB_BACKUP_PREFIX):
                    continue
                p = os.path.join(r, n)
                if os.path.isdir(p) and "未分发" in n and _pk(p) not in seen:
                    seen.add(_pk(p))
                    out.append(p)
        return out

    def on_dub(self):
        if self.app.busy("配乐"):
            return
        folders = self.target_dated_folders()
        if not folders:
            messagebox.showwarning("提示", "这些目标文件夹里还没有 MM-DD未分发 文件夹\n先执行一次分发",
                                   parent=self.winfo_toplevel())
            return
        self.clear_log()
        self.append_log(f"目标文件夹 {len(folders)} 个，进度见下方日志区：")
        self.app.on_dub(folders=folders, log_widget=self.log_text, status_cb=self.set_status)

    def on_rename_only(self):
        if self.app.busy("改名"):
            return
        folders = self.target_dated_folders()
        if not folders:
            messagebox.showwarning("提示", "这些目标文件夹里还没有 MM-DD未分发 文件夹\n先执行一次分发",
                                   parent=self.winfo_toplevel())
            return
        names = load_names(self.app.name_dir.get())
        if not names:
            messagebox.showwarning("提示", f"命名库没有可用的TXT命名：\n{self.app.name_dir.get()}\n"
                                           "请在命名库文件夹放TXT（每行一个命名）。",
                                   parent=self.winfo_toplevel())
            return
        if not messagebox.askyesno(
                "按命名库改名",
                f"将把这{len(folders)}个日期文件夹里的文件\n随机改成命名库的{len(names)}条名字（批内不重复，用完才复用）\n"
                "并写重命名日志，撤回时可退回原名。确定吗？",
                parent=self.winfo_toplevel()):
            return
        allowed = self.allowed()
        self.clear_log()
        self.append_log(f"开始按命名库改名 {len(folders)}个日期文件夹（命名库{len(names)}条）...")
        self.set_status("按命名库改名中...")
        self.app.persist()

        def run():
            try:
                renamed, rows = rename_with_library(folders, names, allowed)
            except Exception as e:
                def _fail():
                    self.set_status(f"改名出错：{e}")
                    messagebox.showerror("改名出错", f"{e}", parent=self.winfo_toplevel())
                self._ui(_fail)
                return
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = self._log_dir([{"path": folders[0]}])
            logp = unique_dest(os.path.join(log_dir, f"distribute_log_rename_{ts}.csv"))
            log_err = None
            try:
                with open(logp, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["原路径", "新路径", "日期文件夹"])
                    w.writerows(rows)
            except Exception as e:
                log_err = e
            failed = sum(1 for r in rows if str(r[1]).startswith("FAILED"))

            def _done():
                self.append_log(f"改名完成：成功{renamed}个，失败{failed}个（日志 {os.path.basename(logp)}）")
                self.set_status(f"改名完成：成功{renamed}个，失败{failed}个")
                if log_err is not None:
                    messagebox.showwarning("警告", f"改名完成了，但日志写入失败：\n{logp}\n{log_err}\n"
                                                  "这次改名将无法撤回。", parent=self.winfo_toplevel())
                messagebox.showinfo("改名完成", f"成功{renamed}个，失败{failed}个\n日志：{os.path.basename(logp)}",
                                    parent=self.winfo_toplevel())
                self.refresh_lib_status()
            self._ui(_done)
        threading.Thread(target=run, daemon=True).start()

    def refresh_lib_status(self):
        """音乐库/命名库在共用栏显示，这里同时给本页按钮旁补一行计数。"""
        try:
            mdir = self.app.music_dir.get()
            ndir = self.app.name_dir.get()
        except Exception:
            return

        def bg():
            try:
                n = len(list_audios(mdir))
                nn = len(load_names(ndir))
                txt = f"音乐{n}首 · 命名{nn}条"
            except Exception:
                return

            def _do():
                try:
                    self.lbl_lib.config(text=txt)
                except Exception:
                    pass
                try:
                    self.app._apply_lib_status(txt)
                except Exception:
                    pass
            self._ui(_do)
        threading.Thread(target=bg, daemon=True).start()

    def _poll_dub(self):
        """有任务在跑时把本页的配乐/改名/撤回按钮禁用，避免并发操作。"""
        try:
            busy = (getattr(self.app, "_dubbing", False) or getattr(self.app, "_executing", False)
                    or getattr(self.app, "_undoing", False) or getattr(self.app, "_rename_busy", False)
                    or getattr(self, "_executing", False))
            st = "disabled" if busy else "normal"
            if str(self.b_dub.cget("state")) != st:
                self.b_dub.config(state=st)
                self.b_rename.config(state=st)
                self.b_undo.config(state=st)
        except Exception:
            pass
        try:
            self.after(600, self._poll_dub)
        except Exception:
            pass

def main():
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
