import os
import sys
import subprocess
import time
import re
import asyncio
from typing import Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
import uvicorn
import socketio

ADB_PATH = "adb"
is_monitoring = True


# 算法模式选项
class AlgorithmMode:
    AUTO = "auto"
    SURFACEFLINGER = "surfaceflinger"
    GFXINFO = "gfxinfo"


GLOBAL_STATE = {
    "package": "等待连接...",
    "package_display": "等待连接...",
    "fps": 0.0,
    "engine": "自动探测",
    "mode": AlgorithmMode.AUTO  # 当前算法模式
}

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

# 全局变量保存后台任务引用
monitor_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global monitor_task
    print("💡 正在等待 ADB 设备连接...")
    monitor_task = asyncio.create_task(monitor_loop())
    yield
    print("正在关闭后台监控任务...")
    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try:
            await monitor_task
            print("监控任务已安全关闭")
        except asyncio.CancelledError:
            print("监控任务已被取消")


app = FastAPI(lifespan=lifespan)
sio_app = socketio.ASGIApp(sio, app)

try:
    with open("index.html", "r", encoding="utf-8") as f:
        HTML_TEMPLATE = f.read()
except FileNotFoundError:
    print("⚠️ 未在当前目录下找到 index.html，将使用内置基本模版替代。")
    HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>FPS监控系统</title>
        <style>
            body { font-family: Arial; padding: 20px; }
            .fps-display { font-size: 48px; font-weight: bold; margin: 20px 0; }
            .controls { margin: 20px 0; }
            button { padding: 10px 20px; margin: 5px; font-size: 16px; cursor: pointer; }
            .active { background-color: #4CAF50; color: white; }
        </style>
    </head>
    <body>
        <h1>🎮 FPS监控系统</h1>
        <div>当前应用: <span id="package">---</span></div>
        <div class="fps-display">FPS: <span id="fps">0.0</span></div>
        <div>算法引擎: <span id="engine">---</span></div>
        <div class="controls">
            <h3>算法切换</h3>
            <button onclick="switchMode('auto')" id="btn-auto">自动探测</button>
            <button onclick="switchMode('surfaceflinger')" id="btn-surface">SurfaceFlinger (游戏)</button>
            <button onclick="switchMode('gfxinfo')" id="btn-gfx">Gfxinfo (应用)</button>
        </div>
        <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
        <script>
            const socket = io();
            let currentMode = 'auto';

            socket.on('fps_update', function(data) {
                document.getElementById('package').innerText = data.package_display;
                document.getElementById('fps').innerText = data.fps;
                document.getElementById('engine').innerText = data.engine;
            });

            function switchMode(mode) {
                currentMode = mode;
                // 高亮当前按钮
                document.getElementById('btn-auto').classList.remove('active');
                document.getElementById('btn-surface').classList.remove('active');
                document.getElementById('btn-gfx').classList.remove('active');
                document.getElementById(`btn-${mode === 'surfaceflinger' ? 'surface' : mode}`).classList.add('active');

                // 发送切换命令
                fetch(`/switch_mode?mode=${mode}`);
            }
        </script>
    </body>
    </html>
    """


class UltimateLockedRadar:
    def __init__(self):
        self.package_name = None
        self.vsync_interval = 16666666
        self.processed_timestamps = set()

        # Gfxinfo 专属变量
        self.last_gfx_frames = None
        self.last_gfx_time = time.time()

        # 当前算法模式 (auto/surfaceflinger/gfxinfo)
        self.current_mode = AlgorithmMode.AUTO

    def set_mode(self, mode: str):
        """手动切换算法模式"""
        if mode in [AlgorithmMode.AUTO, AlgorithmMode.SURFACEFLINGER, AlgorithmMode.GFXINFO]:
            self.current_mode = mode
            # 切换模式时清空缓存数据，避免干扰
            self.processed_timestamps.clear()
            self.last_gfx_frames = None
            return True
        return False

    def check_device(self) -> bool:
        try:
            result = subprocess.run(f"{ADB_PATH} devices", shell=True, capture_output=True, text=True, timeout=2)
            return "device" in result.stdout and "unauthorized" not in result.stdout
        except:
            return False

    def get_foreground_package(self) -> Optional[str]:
        try:
            result = subprocess.run(
                f"{ADB_PATH} shell dumpsys window | findstr \"mCurrentFocus mResumedActivity\"",
                shell=True, capture_output=True, text=True, timeout=2
            )
            if result.stdout:
                match = re.search(r'([\w.]+)/', result.stdout)
                if match:
                    return match.group(1)
        except:
            pass
        return None

    def get_game_layer(self) -> Optional[str]:
        if not self.package_name:
            return None
        try:
            result = subprocess.run(
                f"{ADB_PATH} shell dumpsys SurfaceFlinger --list",
                shell=True, capture_output=True, text=True, timeout=2
            )
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if self.package_name in line and 'SurfaceView' in line and '(BLAST)' in line:
                    match = re.search(r'(SurfaceView\[[^\]]+\]\(BLAST\)#\d+)', line)
                    if match:
                        return match.group(1)
            for line in lines:
                if self.package_name in line and 'SurfaceView' in line:
                    return line.strip()
        except:
            pass
        return None

    def clamp_and_align_fps(self, raw_fps: float) -> float:
        """物理刷新率强制平滑对齐拦截器"""
        if raw_fps <= 0.1:
            return 0.0

        standard_rates = [60.0, 90.0, 120.0, 144.0]
        tolerance = 2.0

        for rate in standard_rates:
            if abs(raw_fps - rate) <= tolerance:
                return rate

        if raw_fps > max(standard_rates):
            return max(standard_rates)

        return raw_fps

    def calculate_game_surface_fps(self) -> float:
        """游戏专属：SurfaceFlinger 纳秒级时钟差算法"""
        layer_name = self.get_game_layer()
        if not layer_name:
            return 0.0

        try:
            cmd = f"{ADB_PATH} shell \"dumpsys SurfaceFlinger --latency '{layer_name}'\""
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2)
            if not result.stdout:
                return 0.0

            lines = result.stdout.strip().split('\n')
            if len(lines) < 3:
                return 0.0

            try:
                self.vsync_interval = int(lines[0].strip())
            except ValueError:
                self.vsync_interval = 16666666

            present_times = []
            for line in lines[1:]:
                parts = line.split()
                if len(parts) == 3:
                    actual_present_time = int(parts[2])
                    if 0 < actual_present_time < 9223372036854775807:
                        present_times.append(actual_present_time)

            if not present_times:
                return 0.0

            new_times = [t for t in present_times if t not in self.processed_timestamps]
            for t in new_times:
                self.processed_timestamps.add(t)

            if len(self.processed_timestamps) > 1000:
                self.processed_timestamps = set(list(self.processed_timestamps)[-500:])

            if len(new_times) < 2:
                return 0.0

            new_times.sort()
            intervals = []
            min_threshold = self.vsync_interval * 0.95

            for i in range(1, len(new_times)):
                interval_ns = new_times[i] - new_times[i - 1]
                if interval_ns < min_threshold:
                    interval_ns = self.vsync_interval
                if interval_ns <= 500_000_000:
                    intervals.append(interval_ns)

            if not intervals:
                return 0.0

            avg_interval_ns = sum(intervals) / len(intervals)
            raw_fps = 1_000_000_000 / avg_interval_ns

            return self.clamp_and_align_fps(raw_fps)
        except:
            return 0.0

    def calculate_app_gfx_fps(self) -> float:
        """应用专属：Gfxinfo 算法"""
        try:
            cmd = f"{ADB_PATH} shell dumpsys gfxinfo {self.package_name}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2)
            match = re.search(r'Total frames rendered:\s*(\d+)', result.stdout)
            current_time = time.time()

            if match:
                current_frames = int(match.group(1))
                if self.last_gfx_frames is not None:
                    frame_diff = current_frames - self.last_gfx_frames
                    time_diff = current_time - self.last_gfx_time

                    self.last_gfx_frames = current_frames
                    self.last_gfx_time = current_time

                    if time_diff > 0 and frame_diff > 0:
                        raw_fps = frame_diff / time_diff
                        return self.clamp_and_align_fps(raw_fps)

                self.last_gfx_frames = current_frames
                self.last_gfx_time = current_time
        except:
            pass
        return 0.0

    def get_display_name(self, pkg: str) -> str:
        if 'aweme' in pkg:
            return "抖音"
        elif 'tencent' in pkg or 'netease' in pkg:
            return f"🎮 游戏 ({pkg.split('.')[-1]})"
        return pkg.split('.')[-1]


radar = UltimateLockedRadar()


async def monitor_loop():
    global GLOBAL_STATE
    while is_monitoring and not radar.check_device():
        await asyncio.sleep(2)
    print("✅ 设备已连接，完美锁帧平滑双模引擎已激活...")

    last_valid_game_fps = 0.0

    while is_monitoring:
        try:
            current_package = radar.get_foreground_package()
            if current_package:
                if current_package != radar.package_name:
                    radar.package_name = current_package
                    radar.processed_timestamps.clear()
                    radar.last_gfx_frames = None
                    last_valid_game_fps = 0.0

                display_name = radar.get_display_name(current_package)
                is_game = 'tencent' in current_package or 'netease' in current_package

                # 根据当前选择的算法模式决定使用哪种算法
                mode = radar.current_mode
                fps = 0.0
                engine_type = ""

                if mode == AlgorithmMode.SURFACEFLINGER:
                    # 强制使用 SurfaceFlinger
                    fps = radar.calculate_game_surface_fps()
                    engine_type = "SurfaceFlinger (游戏物理模式) [手动]"
                    if fps > 0:
                        last_valid_game_fps = fps
                    else:
                        fps = last_valid_game_fps

                elif mode == AlgorithmMode.GFXINFO:
                    # 强制使用 Gfxinfo
                    fps = radar.calculate_app_gfx_fps()
                    engine_type = "Gfxinfo (应用通用模式) [手动]"

                else:  # AUTO 模式
                    if is_game:
                        fps = radar.calculate_game_surface_fps()
                        engine_type = "SurfaceFlinger (游戏物理模式) [自动]"
                        if fps > 0:
                            last_valid_game_fps = fps
                        else:
                            fps = last_valid_game_fps
                    else:
                        fps = radar.calculate_app_gfx_fps()
                        engine_type = "Gfxinfo (应用通用模式) [自动]"

                GLOBAL_STATE = {
                    "package": current_package,
                    "package_display": display_name,
                    "fps": round(fps, 1),
                    "engine": engine_type,
                    "mode": mode
                }
            else:
                GLOBAL_STATE = {
                    "package": "未知",
                    "package_display": "未检测到前台应用",
                    "fps": 0.0,
                    "engine": "自动探测",
                    "mode": radar.current_mode
                }

            await sio.emit('fps_update', GLOBAL_STATE)

        except Exception as e:
            print(f"监测异常: {e}")

        await asyncio.sleep(0.5)


@app.get("/switch_mode")
async def switch_mode(mode: str = Query(..., regex="^(auto|surfaceflinger|gfxinfo)$")):
    """手动切换算法模式的API接口"""
    if radar.set_mode(mode):
        mode_names = {
            AlgorithmMode.AUTO: "自动探测",
            AlgorithmMode.SURFACEFLINGER: "SurfaceFlinger",
            AlgorithmMode.GFXINFO: "Gfxinfo"
        }
        print(f"🔄 算法已切换为: {mode_names[mode]}")
        return {"status": "success", "mode": mode, "message": f"已切换到{mode_names[mode]}模式"}
    return {"status": "error", "message": "切换失败"}


@app.get("/")
async def index_route():
    return HTMLResponse(HTML_TEMPLATE)


if __name__ == "__main__":
    print("请在浏览器中打开: http://127.0.0.1:8000")
    uvicorn.run(sio_app, host="127.0.0.1", port=8000, log_level="warning")
