## 1. Scientific comparison

- [x] 1.1 实施 NCS all-p legacy mode、GUI 解析说明及矛盾参数校验，保留其他 linker 和显式模式。
- [x] 1.2 添加 synthetic 壳层/方向/原子边界与完整 S-正交归一 MO 条件下的旧 SVD 等价测试，覆盖结果持久化。

## 2. Report presentation

- [x] 2.1 从结果补齐只读报告 metadata，兼容历史读取且不改旧 artifact。
- [x] 2.2 恢复配色、布局、侧栏与轨道标记，保留 TXT、尺寸、设置、closed-shell 和 spin-sum 语义；覆盖 focused GUI tests 与图像导出。

## 3. Validation and documentation

- [x] 3.1 同步相关开发说明、更新日志及双语手册，重建并目检两份 PDF。
- [x] 3.2 运行 focused/direct integration、一次 full offline suite、局部图表视觉检查与独立 Claude 只读审查。
- [x] 3.3 完成 apply strict validation；报告实际验证及未验证事项，不提交、不打包、不执行真实 HPC 操作。
