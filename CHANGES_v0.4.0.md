# v0.4.0 变更说明

## 1. 三轴评审协议

所有新评审 issue 必须包含 `review_axis`，且只能取：

- `camera_coverage`：画面是否拍全、物体是否太远、视角是否重复、关键细节是否可见；
- `shape_silhouette`：仪器形态、比例和外轮廓，作为最重要的质量维度；
- `graduations`：刻度外观、贴合、标签，以及代码是否从内腔真实零体积位置开始做体积积分。

摄像机覆盖是前置门槛。视角不足时不得推断部件缺失或几何错误。

## 2. 新增 `retake_views`

`VLMReview.verdict` 现在支持：

- `pass`
- `revise`
- `retake_views`

当所有视角裁切、过远、遮挡严重或角度过于相似时，VLM 应选择 `retake_views`。该决策必须：

- 至少返回一个 `camera_coverage` issue；
- 不得混入 `shape_silhouette` 或 `graduations` issue；
- 返回完整 Blender 脚本；
- 只修改相机位置、目标点、镜头和诊断视角定义，不修改资产几何、材质或刻度计算。

下一轮会直接用新脚本重新渲染一组视角。

## 3. 命令行人工提示

新增参数：

```powershell
--human-hint "人工意见"
--human-hint-from-iteration 3
```

提示从指定轮次开始传给 GPT 看图评审和 Blender 报错修复。提示会保存到 `manifest.json`，续跑时默认继承；在 `resume` 命令中重新提供提示可覆盖原提示。

示例：

```powershell
lab-asset-agent resume runs\<run-id> -c config.yaml `
  --human-hint "瓶颈偏粗，优先核对颈身比例" `
  --human-hint-from-iteration 4
```

## 4. 其他调整

- 初始代码提示要求诊断视角完整、差异明显且细节可读。
- issue history 修正为保存全部 minor / moderate / major / critical issue。
- 旧 manifest 中没有 `review_axis` 的 issue 仍可加载，默认按 `shape_silhouette` 兼容；新模型响应则强制要求显式提供该字段。
- 项目版本统一更新为 `0.4.0`。
- 新增 5 个离线测试，未调用任何付费 API。
