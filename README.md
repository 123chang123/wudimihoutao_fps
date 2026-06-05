本项目是一个基于 Android ADB 的实时帧率监控工具，支持**SurfaceFlinger**和**Gfxinfo**两种 FPS 采集算法，并提供 Web 可视化界面、历史记录、性能报告生成和备注功能。

## 功能特点

-  **ADB 实时监控**：自动检测前台应用。
-  **双算法自动/手动切换**：
  - `SurfaceFlinger`（游戏专用，纳秒级精度）
  - `Gfxinfo`（通用应用模式）
  - `Auto`（根据包名自动选择）
-  **实时图表**：最近 60 秒 FPS 趋势图。
-  **录制分析报告**：生成包含平均帧率、卡顿率、P50/P95/P99、分布图等详细报告。
-  **本地存储历史记录**：支持导出 HTML / JSON / 图表图片。
-  **图表备注功能**：点击趋势图可添加备注，便于性能问题标注。

---
## 使用说明
### 主界面
- 实时状态：显示当前应用、FPS、当前使用的算法引擎。
- 算法切换：可手动选择 自动探测 / SurfaceFlinger / Gfxinfo。
- 录制报告：点击开始录制分析报告，再次点击结束并生成详细分析报告。
- 历史记录：所有录制的报告会保存在浏览器本地，支持查看、导出、删除。

### 报告内容
平均 / 最低 / 最高 / P50 / P95 / P99 帧率
- 标准差、卡顿次数、卡顿率、综合评分
- 帧率分布柱状图（0-30, 30-45, 45-60, 60-90, 90+）
- 趋势图
- 备注管理（添加 / 删除）

---
## 算法说明
|模式|适用场景|原理
|---|---|---|
|SurfaceFlinger|游戏、高刷新率应用|解析 dumpsys SurfaceFlinger --latency，基于实际图层 present 时间计算帧间隔|
|Gfxinfo|普通应用|解析 dumpsys gfxinfo 中的 Total frames rendered，通过差值计算帧率|

---
## 文件说明
```python
├── fps-test-final.py      # 后端主程序
├── index.html             # 前端可视化界面
└── README.md              # 本说明文档
```
- index.html 需与 fps-test-final.py 放在同一目录下，程序启动时会自动读取。
- 修改 ADB 路径：在 fps-test-final.py 中更改 ADB_PATH = "adb"。
- 修改服务端口：在 uvicorn.run(sio_app, host="127.0.0.1", port=8000) 中调整。
- 本项目仅供学习与性能测试使用。

