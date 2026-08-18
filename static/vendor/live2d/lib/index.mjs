import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
//#region src/index.ts
/** Browser-facing base path of Live2D model assets. */
const LIVE2D_PREFIX = "/pet/live2d";
/** MIME map for model assets (served by extension). */
const LIVE2D_MIME = {
	".json": "application/json",
	".moc3": "application/octet-stream",
	".png": "image/png",
	".js": "application/javascript"
};
/** Absolute package root, resolved from this module's own location (lib/). */
function packageRoot(importMetaUrl) {
	return fileURLToPath(new URL("../", importMetaUrl));
}
/** Stable cordis plugin name (matches cordis.patch.yml insert id). */
const name = "live2d-pet";
/** Services required before the plugin can mount its surfaces. */
const inject = ["webServer"];
/** Default model path relative to assets/live2d/. */
const DEFAULT_MODEL = "haru/haru_greeter_t03.model3.json";
function apply(ctx, config = {}) {
	const model = config.model ?? "haru/haru_greeter_t03.model3.json";
	const root = packageRoot(import.meta.url);
	ctx.effect(() => {
		const dispose = ctx.webServer.register({
			kind: "prefix",
			path: LIVE2D_PREFIX,
			handler: async (req, res) => {
				if (req.method !== "GET" && req.method !== "HEAD") {
					res.writeHead(405);
					res.end();
					return;
				}
				const url = new URL(req.url ?? "", "http://local");
				const rel = decodeURIComponent(url.pathname.slice(12));
				const filePath = join(root, "assets", "live2d", rel);
				const rootPath = join(root, "assets", "live2d");
				if (filePath !== rootPath && !filePath.startsWith(rootPath + "\\") && !filePath.startsWith(rootPath + "/")) {
					res.writeHead(403);
					res.end();
					return;
				}
				try {
					const body = await readFile(filePath);
					const ext = filePath.slice(filePath.lastIndexOf(".")).toLowerCase();
					res.writeHead(200, {
						"content-type": LIVE2D_MIME[ext] ?? "application/octet-stream",
						"content-length": String(body.byteLength),
						"cache-control": "no-cache"
					});
					if (req.method === "HEAD") {
						res.end();
						return;
					}
					res.end(body);
				} catch {
					res.writeHead(404);
					res.end();
				}
			}
		});
		return () => {
			dispose();
		};
	}, "live2d-pet: asset routes");
	ctx.effect(() => {
		const dispose = ctx.webServer.tapIndex((html) => {
			const injected = `<script src="${LIVE2D_PREFIX}/live2dcubismcore.min.js"><\/script>`;
			return html.replace(/<head[^>]*>/, (m) => m + injected);
		});
		return () => {
			dispose();
		};
	}, "live2d-pet: core injection");
	ctx.effect(() => {
		const dispose = ctx.webServer.register({
			kind: "exact",
			path: "/pet/live2d/config",
			handler: (req, res) => {
				if (req.method !== "GET" && req.method !== "HEAD") {
					res.writeHead(405);
					res.end();
					return;
				}
				const body = JSON.stringify({
					model: `${LIVE2D_PREFIX}/${model}`,
					size: config.size ?? 320,
					right: config.right ?? 24,
					bottom: config.bottom ?? 20
				});
				res.writeHead(200, {
					"content-type": "application/json; charset=utf-8",
					"content-length": String(Buffer.byteLength(body))
				});
				if (req.method === "HEAD") {
					res.end();
					return;
				}
				res.end(body);
			}
		});
		return () => {
			dispose();
		};
	}, "live2d-pet: config route");
}
//#endregion
export { DEFAULT_MODEL, LIVE2D_PREFIX, apply, inject, name, packageRoot };
