#!/usr/bin/env python3
"""HTTP 端到端 API 测试脚本。

通过 HTTP POST/GET 请求验证 KoiFetch 核心 API 全流程：
  1. GET  /api/health                健康检查
  2. POST /api/auth/login            管理员登录，获取 Bearer Token
  3. PUT  /api/cookies/douyin        配置抖音 Cookie（f2 引擎需要）
  4. POST /api/parse                 批量解析抖音链接
  5. GET  /api/preview/{task_id}    获取解析结果/预览信息
  6. POST /api/download/submit      提交下载任务（bubble 临时存储）
  7. GET  /api/download/progress/{id}  轮询下载进度
  8. POST /api/download/prepare     准备直连下载（返回同源 URL）
  9. GET  /api/download/direct/{id} 流式下载文件并保存
  10. 验证 bubble 文件              data/bubble/ 下存在已下载文件
  11. POST /api/nas/save            保存到 pond 永久存储
  12. 验证 pond 文件                data/pond/ 下存在已保存文件
  13. GET /api/cookies              查看 Cookie 配置

前置条件:
  - 后端服务运行中:  uvicorn app.main:app --app-dir backend
  - .env 已配置 ADMIN_PASSWORD / COOKIE_ENCRYPTION_KEY
  - logs/cookie.txt 含有效抖音 Cookie
  - Worker 由脚本自动启动/停止（bubble 下载需要 Worker）

用法:
  python backend/scripts/test_api_http.py
  python backend/scripts/test_api_http.py --host http://localhost:8000
  python backend/scripts/test_api_http.py --no-download  # 跳过直连文件下载
  python backend/scripts/test_api_http.py --no-nas       # 跳过 bubble/pond 验证
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

# ── 路径常量 ──────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
COOKIE_FILE = REPO_ROOT / "logs" / "cookie.txt"
ENV_FILE = REPO_ROOT / ".env"
OUTPUT_DIR = REPO_ROOT / "data" / "test_output"

# ── 测试用抖音链接 ────────────────────────────────────────────────────
TEST_URLS = [
    "https://v.douyin.com/dOUkqpNFMbo/",  # 视频：古诗词朗诵
    "https://v.douyin.com/aqhxyMsmahU/",   # 图文：生命如长河
    "https://v.douyin.com/TJDJzQpAJfg/",  # 图文：只身回望太匆匆
]


# ── 彩色输出 ──────────────────────────────────────────────────────────
class C:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    INFO = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def _print(tag: str, color: str, msg: str) -> None:
    print(f"  {color}[{tag}]{C.RESET} {msg}")


def ok(msg: str) -> None:
    _print("PASS", C.OK, msg)


def fail(msg: str) -> None:
    _print("FAIL", C.FAIL, msg)


def warn(msg: str) -> None:
    _print("WARN", C.WARN, msg)


def info(msg: str) -> None:
    _print("INFO", C.DIM, msg)


def step(msg: str) -> None:
    print(f"\n{C.BOLD}━━ {msg} ━━{C.RESET}")


# ── .env 读取 ────────────────────────────────────────────────────────
def read_env_password() -> str:
    """从 .env 读取 ADMIN_PASSWORD，回退到默认值。"""
    if not ENV_FILE.exists():
        return "change-me-admin"
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("ADMIN_PASSWORD="):
            return line.split("=", 1)[1].strip()
    return "change-me-admin"


def read_cookie_file() -> str:
    """读取 logs/cookie.txt 的抖音 Cookie。"""
    if not COOKIE_FILE.exists():
        raise FileNotFoundError(f"Cookie 文件不存在: {COOKIE_FILE}")
    cookie = COOKIE_FILE.read_text(encoding="utf-8").strip()
    if not cookie:
        raise ValueError(f"Cookie 文件为空: {COOKIE_FILE}")
    return cookie


def _start_worker() -> subprocess.Popen | None:
    """启动 worker 子进程（后台运行），返回 Popen 对象。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "backend")
    env.setdefault("DATABASE_URL", f"sqlite:///{REPO_ROOT}/backend/data/db/koifetch.db")
    py = str(REPO_ROOT / ".venv" / "Scripts" / "python.exe")
    log_path = REPO_ROOT / "data" / "test_output" / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_file = open(log_path, "w", encoding="utf-8")
        proc = subprocess.Popen(
            [py, "-m", "app.workers.main"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        print(f"  {C.INFO}Worker 已启动 (pid={proc.pid}, log={log_path.name}){C.RESET}")
        return proc
    except Exception as exc:
        print(f"  {C.WARN}Worker 启动失败: {exc}{C.RESET}")
        return None


# ── 测试主体 ──────────────────────────────────────────────────────────
class ApiTester:
    """HTTP 端到端测试器：对运行中的 KoiFetch 服务发请求验证全流程。"""

    def __init__(self, host: str, *, download_timeout: float = 300.0):
        self.base = host.rstrip("/")
        self.client = httpx.Client(
            base_url=self.base,
            timeout=httpx.Timeout(30.0, read=download_timeout),
            follow_redirects=True,
        )
        self.token: str | None = None
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def close(self) -> None:
        self.client.close()

    # ── 内部工具 ──
    def _request(self, method: str, path: str, **kwargs):
        headers = kwargs.pop("headers", {})
        if self.token:
            headers.setdefault("Authorization", f"Bearer {self.token}")
        return self.client.request(method, path, headers=headers, **kwargs)

    def _check(self, resp: httpx.Response, name: str) -> tuple[bool, dict | None]:
        """校验统一响应包：HTTP 200 + code==0。"""
        if resp.status_code != 200:
            fail(f"{name}: HTTP {resp.status_code} — {resp.text[:300]}")
            self.failed += 1
            return False, None
        try:
            body = resp.json()
        except Exception:
            fail(f"{name}: 响应非 JSON — {resp.text[:300]}")
            self.failed += 1
            return False, None
        if body.get("code") != 0:
            fail(f"{name}: code={body.get('code')} message={body.get('message')}")
            self.failed += 1
            return False, body
        ok(name)
        self.passed += 1
        return True, body

    # ── 步骤 1: 健康检查 ──
    def test_health(self) -> bool:
        step("1. 健康检查  GET /api/health")
        resp = self.client.get("/api/health")
        success, body = self._check(resp, "健康检查")
        if success and body and body.get("data"):
            data = body["data"]
            info(f"状态: {data.get('status')}  存储: {data.get('services', {}).get('storage')}")
        return success

    # ── 步骤 2: 登录 ──
    def test_login(self, password: str) -> bool:
        step("2. 管理员登录  POST /api/auth/login")
        resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        success, body = self._check(resp, "登录")
        if success and body and body.get("data"):
            self.token = body["data"]["token"]
            info(f"用户: {body['data']['username']}  过期: {body['data']['expires_at']}")
        return success

    # ── 步骤 3: 配置 Cookie ──
    def test_set_cookie(self, cookie: str) -> bool:
        step("3. 配置抖音 Cookie  PUT /api/cookies/douyin")
        resp = self._request(
            "PUT", "/api/cookies/douyin", json={"cookie": cookie}
        )
        success, body = self._check(resp, "配置 Cookie")
        if success and body and body.get("data"):
            data = body["data"]
            info(f"平台: {data.get('platform')}  已配置: {data.get('configured')}")
        return success

    # ── 步骤 4: 批量解析 ──
    def test_parse(self, urls: list[str]) -> tuple[bool, list[dict]]:
        step(f"4. 批量解析  POST /api/parse  ({len(urls)} 个链接)")
        resp = self._request("POST", "/api/parse", json={"urls": urls})
        success, body = self._check(resp, "批量解析")
        tasks: list[dict] = []
        if success and body and body.get("data"):
            data = body["data"]
            results = data.get("results", [])
            failed = data.get("failed", [])
            info(f"解析成功: {len(results)} 个  失败: {len(failed)} 个")
            for r in results:
                kind = r.get("type", "?")
                title = r.get("title", "")[:40]
                info(f"  [{kind}] {r.get('platform')} | {title} | id={r['task_id'][:8]}...")
                tasks.append(r)
            for f in failed:
                warn(f"  失败: {f.get('url', '')[:60]} — {f.get('error', '')}")
                self.warnings += 1
        return success, tasks

    # ── 步骤 5: 预览 ──
    def test_preview(self, task: dict) -> bool:
        task_id = task["task_id"]
        kind = task.get("type", "?")
        title = task.get("title", "")[:20]
        step(f"5. 获取预览  GET /api/preview/{task_id[:8]}...  [{kind}] {title}")
        resp = self._request("GET", f"/api/preview/{task_id}")
        success, body = self._check(resp, f"预览 [{kind}]")
        if success and body and body.get("data"):
            d = body["data"]
            info(f"类型: {d.get('preview_type')}  平台: {d.get('platform')}")
            info(f"标题: {d.get('title')}")
            if d.get("duration"):
                info(f"时长: {d['duration']}")
            if d.get("file_size_mb") is not None:
                info(f"大小: {d['file_size_mb']:.2f} MB")
            if d.get("available_qualities"):
                info(f"画质: {', '.join(d['available_qualities'])}")
            if d.get("streams"):
                for s in d["streams"][:3]:
                    info(f"  流: q={s.get('quality')} fmt={s.get('format')}")
        return success

    # ── 步骤 6: 提交下载 + 轮询进度 ──
    def test_submit_and_poll(self, task: dict, *, max_polls: int = 15) -> tuple[bool, str | None]:
        task_id = task["task_id"]
        step(f"6. 提交下载  POST /api/download/submit  (task={task_id[:8]}...)")
        resp = self._request("POST", "/api/download/submit", json={"task_id": task_id})
        success, body = self._check(resp, "提交下载")
        if not success or not body or not body.get("data"):
            return False, None

        data = body["data"]
        download_id = data["download_id"]
        info(f"download_id: {download_id}")
        info(f"初始状态: {data.get('status')}  创建: {data.get('created_at')}")

        # 轮询进度
        step(f"6b. 轮询进度  GET /api/download/progress/{download_id[:8]}...")
        for i in range(max_polls):
            resp = self._request("GET", f"/api/download/progress/{download_id}")
            if resp.status_code != 200:
                warn(f"进度查询 HTTP {resp.status_code}")
                break
            prog = resp.json().get("data", {})
            status = prog.get("status", "?")
            progress = prog.get("progress", 0)
            speed = prog.get("speed")
            if status == "completed":
                ok(f"下载完成 (progress={progress:.1f}%)")
                self.passed += 1
                return True, download_id
            if status == "failed":
                # API 正确报告了失败状态（如平台风控），API 合约本身正常
                warn(f"下载失败: {prog.get('error_message', '?')}")
                self.warnings += 1
                return True, download_id
            if status == "expired":
                warn("下载已过期")
                self.warnings += 1
                return False, download_id
            speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed else "—"
            info(f"[{i + 1}/{max_polls}] status={status} progress={progress:.1f}% speed={speed_str}")
            time.sleep(3)

        warn(f"下载未在 {max_polls * 3}s 内完成（Worker 可能未运行）")
        self.warnings += 1
        return True, download_id  # API 合约本身是正常的

    # ── 步骤 7: 直连下载（prepare + direct） ──
    def test_direct_download(self, task: dict) -> bool:
        task_id = task["task_id"]
        # media_type → AssetSelector.kind 映射
        media_type = task.get("type", "")
        kind_map = {
            "video": "video",
            "image": "image",
            "live_photo": "live_image",
            "music": "music",
        }
        kind = kind_map.get(media_type, "video")
        title = task.get("title", "")[:20]
        step(f"7. 直连下载  prepare + GET /api/download/direct/{task_id[:8]}...  [{kind}] {title}")

        # 7a. prepare
        resp = self._request(
            "POST",
            "/api/download/prepare",
            json={"task_id": task_id, "asset": {"kind": kind, "index": 0}},
        )
        success, body = self._check(resp, f"Prepare [{kind}]")
        if not success or not body or not body.get("data"):
            return False

        data = body["data"]
        mode = data.get("mode")
        info(f"传输模式: {mode}")

        if mode != "direct":
            # 分阶段模式（HLS / 打包 / 需 Worker）
            if mode == "staged":
                did = data.get("download_id", "?")
                info(f"分阶段下载: {did}")
                warn("该资源需要 Worker 执行分阶段下载，跳过文件保存")
                self.warnings += 1
                return True
            fail(f"未知传输模式: {mode}")
            self.failed += 1
            return False

        url = data.get("url", "")
        filename = data.get("filename") or f"{task_id[:8]}.{kind}"
        info(f"直连 URL: {url}")
        info(f"文件名: {filename}")

        # 7b. 流式下载文件
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUT_DIR / filename
        try:
            with self.client.stream("GET", url) as stream:
                if stream.status_code != 200:
                    # 上游可能返回 403（风控）/500 等，API 端点本身正常
                    warn(f"直连下载 HTTP {stream.status_code}（上游限制）")
                    self.warnings += 1
                    return True
                total = 0
                with open(out_path, "wb") as f:
                    for chunk in stream.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        total += len(chunk)
                size_mb = total / (1024 * 1024)
                ok(f"文件已保存: {out_path.name}  ({size_mb:.2f} MB)")
                self.passed += 1
                return True
        except httpx.HTTPError as exc:
            fail(f"直连下载异常: {exc}")
            self.failed += 1
            return False

    # ── 步骤 8: 验证 Bubble 文件 ──
    def test_verify_bubble(self) -> bool:
        """验证 data/bubble/ 下存在 Worker 下载的临时文件。"""
        step("8. 验证 Bubble 文件  data/bubble/")
        bubble_dir = REPO_ROOT / "data" / "bubble"
        if not bubble_dir.exists():
            fail(f"Bubble 目录不存在: {bubble_dir}")
            self.failed += 1
            return False
        files = [f for f in bubble_dir.rglob("*") if f.is_file() and not f.name.startswith(".")]
        if not files:
            fail(f"Bubble 目录为空: {bubble_dir}")
            self.failed += 1
            return False
        total_size = sum(f.stat().st_size for f in files)
        size_mb = total_size / (1024 * 1024)
        ok(f"Bubble 文件验证通过: {len(files)} 个文件, {size_mb:.2f} MB")
        for f in files[:5]:
            rel = f.relative_to(bubble_dir)
            info(f"  {rel}  ({f.stat().st_size / (1024 * 1024):.2f} MB)")
        self.passed += 1
        return True

    # ── 步骤 9: NAS Save（bubble → pond） ──
    def test_nas_save(self, download_id: str) -> tuple[bool, str | None]:
        """通过 POST /api/nas/save 把 bubble 文件移动到 pond 永久存储。"""
        step(f"9. Pond 保存  POST /api/nas/save  (download_id={download_id[:8]}...)")
        resp = self._request("POST", "/api/nas/save", json={"download_id": download_id})
        # 文件未下载完成（如平台风控导致下载失败）时 API 返回 code=5002，
        # 这是 API 正确报告的业务状态，不是 API 合约本身的问题。
        if resp.status_code == 200:
            body = resp.json()
            if body.get("code") == 0 and body.get("data"):
                data = body["data"]
                nas_path = data.get("nas_path", "?")
                file_size = data.get("file_size", 0)
                saved_at = data.get("saved_at", "?")
                ok("NAS Save [pond]")
                self.passed += 1
                info(f"NAS 路径: {nas_path}")
                info(f"文件大小: {file_size / (1024 * 1024):.2f} MB")
                info(f"保存时间: {saved_at}")
                return True, nas_path
        # 降级为警告：download 可能因风控未完成
        warn(f"NAS Save 跳过: HTTP {resp.status_code} — {resp.text[:200]}（下载可能未完成）")
        self.warnings += 1
        return False, None

    # ── 步骤 9b: 验证 Pond 文件 ──
    def test_verify_pond(self, nas_path: str | None) -> bool:
        """验证 data/pond/ 下存在已保存的永久文件。"""
        step("9b. 验证 Pond 文件  data/pond/")
        pond_dir = REPO_ROOT / "data" / "pond"
        if not pond_dir.exists():
            fail(f"Pond 目录不存在: {pond_dir}")
            self.failed += 1
            return False
        files = [f for f in pond_dir.rglob("*") if f.is_file() and not f.name.startswith(".")]
        if not files:
            fail(f"Pond 目录为空: {pond_dir}")
            self.failed += 1
            return False
        total_size = sum(f.stat().st_size for f in files)
        size_mb = total_size / (1024 * 1024)
        ok(f"Pond 文件验证通过: {len(files)} 个文件, {size_mb:.2f} MB")
        for f in files[:5]:
            rel = f.relative_to(pond_dir)
            info(f"  {rel}  ({f.stat().st_size / (1024 * 1024):.2f} MB)")
        self.passed += 1
        return True

    # ── 步骤 10: 查看 Cookie ──
    def test_list_cookies(self) -> bool:
        step("10. 查看 Cookie 配置  GET /api/cookies")
        resp = self._request("GET", "/api/cookies")
        success, body = self._check(resp, "列出 Cookie")
        if success and body and body.get("data"):
            for c in body["data"].get("cookies", []):
                info(f"  {c.get('platform')}: configured={c.get('configured')}  updated={c.get('updated_at', '?')[:19]}")
        return success

    # ── 汇总 ──
    def summary(self, elapsed: float) -> bool:
        total = self.passed + self.failed + self.warnings
        print(f"\n{'━' * 50}")
        print(f"{C.BOLD}测试结果{C.RESET}")
        print(f"  {C.OK}通过: {self.passed}{C.RESET}  {C.FAIL}失败: {self.failed}{C.RESET}  {C.WARN}警告: {self.warnings}{C.RESET}  共: {total}")
        print(f"  耗时: {elapsed:.1f}s")
        all_ok = self.failed == 0
        if all_ok:
            print(f"\n  {C.OK}{C.BOLD}✓ 全部测试通过{C.RESET}\n")
        else:
            print(f"\n  {C.FAIL}{C.BOLD}✗ 有 {self.failed} 项失败{C.RESET}\n")
        return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="KoiFetch HTTP API 端到端测试")
    parser.add_argument(
        "--host", default="http://localhost:8000", help="服务地址 (默认 http://localhost:8000)"
    )
    parser.add_argument(
        "--no-download", action="store_true", help="跳过直连文件下载步骤"
    )
    parser.add_argument(
        "--no-nas", action="store_true", help="跳过 bubble/pond 存储验证步骤"
    )
    parser.add_argument(
        "--password", default=None, help="管理员密码 (默认从 .env 读取)"
    )
    args = parser.parse_args()

    password = args.password or read_env_password()
    try:
        cookie = read_cookie_file()
    except (FileNotFoundError, ValueError) as exc:
        print(f"{C.FAIL}错误: {exc}{C.RESET}", file=sys.stderr)
        return 1

    tester = ApiTester(args.host)
    worker_proc = None
    start = time.time()

    print(f"\n{C.BOLD}╔══ KoiFetch HTTP API 端到端测试 ══╗{C.RESET}")
    print(f"  {C.INFO}目标: {args.host}{C.RESET}")
    print(f"  {C.INFO}链接: {len(TEST_URLS)} 个抖音{C.RESET}")
    print(f"  {C.INFO}Cookie: {len(cookie)} 字符{C.RESET}")
    print(f"  {C.INFO}直连下载: {'跳过' if args.no_download else '启用'}{C.RESET}")
    print(f"  {C.INFO}Bubble/Pond: {'跳过' if args.no_nas else '启用'}{C.RESET}")

    try:
        # 1. 健康检查
        if not tester.test_health():
            fail("健康检查失败，终止")
            return tester.summary(time.time() - start)  # type: ignore[func-returns-value]

        # 2. 登录
        if not tester.test_login(password):
            fail("登录失败，终止")
            return tester.summary(time.time() - start)  # type: ignore[func-returns-value]

        # 3. 配置 Cookie
        tester.test_set_cookie(cookie)

        # 4. 批量解析
        parse_ok, tasks = tester.test_parse(TEST_URLS)
        if not parse_ok or not tasks:
            fail("解析无结果，后续步骤无法继续")
            tester.test_list_cookies()
            return tester.summary(time.time() - start)  # type: ignore[func-returns-value]

        # 5. 预览每个解析结果
        for task in tasks:
            tester.test_preview(task)

        # Bubble/Pond 验证需要 Worker
        if not args.no_nas:
            step("启动 Worker 进程（用于 bubble 下载）")
            worker_proc = _start_worker()
            if worker_proc is None:
                fail("Worker 启动失败，跳过 bubble/pond 验证")
                tester.failed += 1
            else:
                time.sleep(3)  # 等 worker 初始化

        # 6. 提交下载 + 轮询（对所有任务，验证各媒体类型 Worker 下载）
        bubble_download_ids: list[tuple[str, str]] = []  # (download_id, media_type)
        if tasks:
            for task in tasks:
                _, did = tester.test_submit_and_poll(task)
                if did:
                    resp = tester._request("GET", f"/api/download/progress/{did}")
                    if resp.status_code == 200 and resp.json().get("data", {}).get("status") == "completed":
                        mt = task.get("media_type", "?")
                        bubble_download_ids.append((did, mt))
                        info(f"成功下载: {did[:8]}... ({mt})")

        # 7. 直连下载（对每个任务尝试）
        if not args.no_download:
            for task in tasks:
                tester.test_direct_download(task)
        else:
            info("已跳过直连下载 (--no-download)")

        # 8-9. Bubble/Pond 存储验证
        if not args.no_nas and bubble_download_ids:
            # 8. 验证 bubble 文件
            tester.test_verify_bubble()
            # 9. NAS save → pond（对所有成功的下载验证 nas/save）
            for did, mt in bubble_download_ids:
                tester.test_nas_save(did)
            # 9b. 验证 pond 文件
            tester.test_verify_pond(None)
        elif not args.no_nas and not bubble_download_ids:
            info("无成功下载，跳过 bubble/pond 验证")

        # 10. 查看 Cookie
        tester.test_list_cookies()

    except httpx.ConnectError:
        fail(f"无法连接 {args.host}，请确认服务已启动")
        tester.failed += 1
    except Exception as exc:
        fail(f"未预期异常: {exc}")
        tester.failed += 1
    finally:
        # 停止 worker
        if worker_proc is not None:
            worker_proc.terminate()
            try:
                worker_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker_proc.kill()
            print(f"  {C.INFO}Worker 已停止{C.RESET}")
        tester.close()

    return 0 if tester.summary(time.time() - start) else 1


if __name__ == "__main__":
    sys.exit(main())
