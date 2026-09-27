# Hiveswarm web app

Vite + React + TypeScript, Tailwind v4, Radix primitives, xterm.js, Motion.

The built app ships inside the Python package as `src/hiveswarm/ui_dist/`, so installing Hiveswarm with `uv tool install` needs no Node. Rebuild only when you change the app:

```bash
cd webapp
npm install
npm run build
rm -rf ../src/hiveswarm/ui_dist && cp -r dist ../src/hiveswarm/ui_dist
```

Development with live reload against a running `hm ui` on port 7790:

```bash
npm run dev
```
