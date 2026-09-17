## Purpose

让 desktop selection controls 明确表现其可选项，并将视图导出缩放限制为产品实际支持和展示的离散倍率。

## ADDED Requirements

### Requirement: View export offers discrete scale choices
View export dialog SHALL 仅提供 `1x`、`2x`、`4x` 和 `8x` 四个 resolution scale choices，并将选中项的整数倍率传递给现有 export request。

#### Scenario: Export dialog opens
- **WHEN** 用户打开 view export dialog
- **THEN** scale control 是 dropdown，依次显示 `1x`、`2x`、`4x` 和 `8x`
- **THEN** 默认选择 `1x`

#### Scenario: User exports at an offered scale
- **WHEN** 用户选择 `2x`、`4x` 或 `8x` 并确认 export
- **THEN** export request 分别接收整数 `2`、`4` 或 `8` 作为 `scale_factor`
- **THEN** 现有 renderer、format behavior 和 `1..8` request validation 保持不变

#### Scenario: Unsupported intermediate scale is considered
- **WHEN** 用户使用 view export dialog
- **THEN** dialog 不提供 `3x`、`5x`、`6x` 或 `7x` choices

### Requirement: Combo selection controls retain a visible dropdown indicator
所有使用 global theme 的 combo/dropdown selection controls SHALL 在右侧显示清晰可见且适配当前 theme 的 dropdown indicator，除非该 control 有明确记录的例外理由。

#### Scenario: Standard combo is shown in any supported theme
- **WHEN** standard combo selection control 在任一受支持 light 或 dark theme 中显示
- **THEN** control 右侧显示与 theme 有足够对比度的 dropdown arrow

#### Scenario: Combo subclass is shown
- **WHEN** `QFontComboBox` 或其他继承全局 combo styling 的 selection control 显示
- **THEN** control 保留同样可见的右侧 dropdown arrow

#### Scenario: Non-combo tool button is shown
- **WHEN** toolbar 或其他非-combo `QToolButton` 显示其既有 menu indicator
- **THEN** combo dropdown styling 不改变该 indicator 的 behavior 或 appearance
