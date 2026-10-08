# 换电脑 / 迁移指南

配置只保存在**一个文件**里，换电脑时把它拷过去就行。

## 配置在哪

按优先级依次查找，找到第一个存在的就用：

1. `%APPDATA%\VideoDistributor\video_distributor_config.json`
   （即 `C:\Users\<你的用户名>\AppData\Roaming\VideoDistributor\video_distributor_config.json`）
2. 程序同目录下的 `video_distributor_config.json`
3. 配置不存在时用默认值

里面存了这些：分发位置、起始日期、每组数量、间隔天数、改名开关、
分发类型（视频/图片/视频+图片）、打乱开关、音乐库路径、去原声、
配乐时改名、命名库路径、当前页面。

## 需要一起拷的东西

| 要拷 | 说明 |
| --- | --- |
| `video_distributor_config.json` | 设置本身（最关键） |
| `音乐库/` | 你的音乐（注意版权，仅在自己机器间拷贝） |
| `命名库/` | 命名 TXT |
| `ffmpeg.exe` | 从 [ffmpeg.org](https://ffmpeg.org/download.html) 重新下载更稳妥 |

## 不需要拷的

- `distribute_log_*.csv` —— 这是**撤回用的日志**，只在原机器上有效。
  换机器后撤回找不到原路径，别指望它跟着走。
- `.配乐原片备份_*` 目录 —— 同上，配乐原片备份只对原机器有意义。
- `外部依赖/`（历史遗留的本地大模型运行时）—— 当前版本不依赖它。

## 步骤

1. 在新电脑上装 Python 3.9+（Windows 自带 tkinter 即可）
2. clone 或拷贝本仓库
3. 可选：`pip install tkinterdnd2`（为了鼠标拖拽）
4. 把旧机器的 `video_distributor_config.json` 放到 `%APPDATA%\VideoDistributor\` 下
   （没有这个文件夹就自己建一个）
5. 拷贝 `音乐库/`、`命名库/`，并放好 `ffmpeg.exe`
6. 运行 `python video_distributor.py`
7. 打开后检查底部「🎵 音乐库 / 命名库」那栏：
   - 「音乐X首 · 命名Y条」计数对不对
   - `ffmpeg:可用` 有没有显示
8. 顺手点一次【预览分配】确认路径都对

## 如果配置丢了

不用重设，全部可以在界面上点着改完：

- 分发位置、日期规则 → 顶部分发规则区
- 音乐库、命名库 → 底部共用栏
- 其余都是开关

配置会在改动时自动保存（先写临时文件再原子替换，断电不会写坏）。