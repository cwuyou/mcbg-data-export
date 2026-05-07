# Icons

请放置三个 PNG 图标：

- `icon16.png` — 16x16
- `icon48.png` — 48x48
- `icon128.png` — 128x128

MVP 阶段可以随便生成纯色方块占位（在 https://via.placeholder.com 之类或用 Python:
`python -c "from PIL import Image; [Image.new('RGB',(s,s),(22,119,255)).save(f'icon{s}.png') for s in (16,48,128)]"`)。

没有 icon 文件时 Chrome 扩展不会加载。
