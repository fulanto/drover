"""heavy 按资源调度（按实时 CPU 放行）：容量按性能核、需求按实测，机器有余量就跑、
整机或 heavy 实测满了才排队、没有历史的先按 1 核、小命令不排队、防饿死（只放能在排头开跑前跑完的）、持有者被杀后回收、
被中断时结束子进程树、--status；整机 CPU 的读法、进程树 CPU 的算法；accept 的多条验证同时跑（结果顺序不变、--serial
逐条）、基线准备不占预留、建图和复核进调度；codex worker 的可写目录；brief 的新约束。

不靠真实负载和长 sleep：
- 放行判定用 heavy_plan 纯函数测（注入时间、内存信号、整机实测和票里的进程树实测）。
- 端到端用假命令 hold.py：开始写 <名字>.start；有 <名字>.hold 时一直等到测试写 <名字>.release 才结束，结束写 <名字>.end。
  实测历史（占几核、跑多久）直接写进临时状态目录的 history.json，容量写进临时 config.json（4 核），整机实测是假采样文件
  （DROVER_HEAVY_CPU_FILE，selftest_support 默认写 0），和本机负载无关。
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

from selftest_support import DROVER, DroverTestCase, wait_for

HOLD = """import subprocess, sys, time
from pathlib import Path
name, root = sys.argv[1], Path(sys.argv[2])
(root / (name + ".start")).write_text(repr(time.time()))
if (root / (name + ".child")).exists():
    child = subprocess.Popen([sys.executable, "-c", "import time\\nwhile True: time.sleep(0.1)"])
    (root / (name + ".gpid")).write_text(str(child.pid))
deadline = time.time() + 120
while (root / (name + ".hold")).exists() and not (root / (name + ".release")).exists() and time.time() < deadline:
    time.sleep(0.05)
(root / (name + ".end")).write_text(repr(time.time()))
"""

# accept 的验证命令：开始写 <名字>.start，等另一条也开始（最多等 <标记目录>/wait 里写的秒数，没有这个文件就是 10 秒；
# 等不到照样往下），结束写 <名字>.end。同时跑时两条互相等到、时间上重叠（等待上限只在出错时才用满）；
# 逐条跑时第一条等满上限才结束，第二条在它之后开始（--serial 的用例把上限设成 1 秒）。
RENDEZVOUS = """import sys, time
from pathlib import Path
name, other, root = sys.argv[1], sys.argv[2], Path(sys.argv[3])
(root / (name + ".start")).write_text(repr(time.time()))
limit = float((root / "wait").read_text()) if (root / "wait").exists() else 10.0
deadline = time.time() + limit
while not (root / (other + ".start")).exists() and time.time() < deadline:
    time.sleep(0.02)
(root / (name + ".end")).write_text(repr(time.time()))
print("done", name)
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SchedTestCase(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.heavy = self.state / "locks" / "heavy"
        self.marks = self.tmp / "marks"
        self.marks.mkdir()
        self.hold = self.tmp / "hold.py"
        self.hold.write_text(HOLD)
        # 宽限期缩到 1 秒：刚放行的按预留算的那段，端到端用例不用等默认的 5 秒
        self.set_config(heavy_cpu_capacity=4, heavy_grace_seconds=1)
        self.d = self.load_drover()

    def set_config(self, **values) -> None:
        path = self.state / "config.json"
        cfg = json.loads(path.read_text())
        cfg.update(values)
        path.write_text(json.dumps(cfg))

    def seed(self, argv: list, cwd: Path, *, cores: float, wall: float = 30.0, mem: int = 100) -> dict:
        """在历史里写一次实测（命令形状和这条命令各一份，同 heavy_history_add）：这条命令占 cores 核、跑 wall 秒。返回命令形状。"""
        shape = self.d.heavy_shape(argv, str(cwd))
        path = self.heavy / "history.json"
        data = json.loads(path.read_text()) if path.exists() else {"keys": {}, "exact": {}}
        runs = [{"at": time.time(), "wall": wall, "cpu": cores * wall, "mem_mb": mem, "exit": 0}]
        data["keys"][shape["key"]] = {"family": shape["family"], "shape": shape["shape"], "repo": shape["repo"],
                                      "last": time.time(), "runs": runs}
        data["exact"][shape["exact_key"]] = {"command": shape["exact"], "repo": shape["repo"], "last": time.time(),
                                             "runs": runs, "solo": []}
        self.heavy.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return shape

    def history(self) -> dict:
        path = self.heavy / "history.json"
        return json.loads(path.read_text())["keys"] if path.exists() else {}

    def hold_argv(self, name: str) -> list:
        return [sys.executable, str(self.hold), name, str(self.marks)]

    def start(self, name: str, *, hold: bool = True, child: bool = False) -> subprocess.Popen:
        """后台跑 drover heavy -- hold.py <名字>；hold 时等测试写 <名字>.release 才结束。"""
        if hold:
            (self.marks / f"{name}.hold").write_text("")
        if child:
            (self.marks / f"{name}.child").write_text("")
        err = (self.tmp / f"{name}.stderr").open("w")
        p = subprocess.Popen([sys.executable, str(DROVER), "heavy", "--cwd", str(self.tmp), "--", *self.hold_argv(name)],
                             env=self.env, cwd=str(self.tmp), stdout=subprocess.DEVNULL, stderr=err)
        err.close()
        self.addCleanup(self.cleanup_proc, p, name)
        return p

    def cleanup_proc(self, p: subprocess.Popen, name: str) -> None:
        (self.marks / f"{name}.release").write_text("")
        if p.poll() is None:
            p.kill()
            p.wait(10)
        gpid = self.marks / f"{name}.gpid"
        if gpid.exists() and alive(int(gpid.read_text())):
            os.kill(int(gpid.read_text()), signal.SIGKILL)

    def started(self, name: str) -> bool:
        return (self.marks / f"{name}.start").exists()

    def stderr(self, name: str) -> str:
        return (self.tmp / f"{name}.stderr").read_text()

    def release(self, name: str, p: subprocess.Popen) -> None:
        (self.marks / f"{name}.release").write_text("")
        self.assertEqual(p.wait(30), 0, self.stderr(name))

    def tickets(self) -> list:
        return sorted(self.heavy.glob("run-*.json"))


def ticket(tid: str, state: str, reserve: float, *, at: float = 0.0, exclusive: bool = False, small: bool = False,
           mem_mb: float = 0, cores: float = None, started: float = None, cpu_now: float = None, cpu_at: float = 100.0,
           wall: float = None) -> dict:
    """一张票：cpu_now 是属主在 cpu_at 时刻采到的进程树实测（默认 100 秒，MeasuredPlanTests 的"现在"）；wall 是历史最长墙钟。"""
    t = {"id": tid, "state": state, "reserve": reserve, "exclusive": exclusive, "small": small,
         "enqueued_at": at, "command": f"cmd-{tid}", "mem_mb": mem_mb, "cores": cores, "started_at": started,
         "wall": wall}
    if cpu_now is not None:
        t.update(cpu_now=cpu_now, cpu_at=cpu_at)
    return t


NORMAL = {"tight": False, "available_mb": None, "text": "内存压力正常"}
UNREADABLE = {"busy": None, "logical": 10, "text": "整机 CPU 读不到"}


def cpu(busy: float, logical: int = 10) -> dict:
    return {"busy": busy, "logical": logical, "text": f"整机实测忙 {busy} / {logical} 核"}


class PlanTests(SchedTestCase):
    """放行判定 heavy_plan（纯函数，容量 4 核，放行上限 4 × 1.2 = 4.8 核）。这一组读不到整机 CPU、票里也没有实测：
    退化成按预留（codex 沙箱以外读不到整机的情形）。按实测的在 MeasuredPlanTests。"""

    def plan(self, tickets: list, *, mem: dict = NORMAL, now: float = 10.0, starve: float = 120.0,
             cpu: dict = UNREADABLE) -> tuple:
        return self.d.heavy_plan(tickets, 4.0, mem, now, starve, cpu)

    def test_light_commands_share_capacity(self) -> None:
        """合计 ≤ 放行上限的同时跑；再加一条超过的排队，原因写要多少、已占多少、上限怎么来的"""
        admitted, reasons = self.plan([ticket("a", "running", 1.4), ticket("b", "queued", 1.7, at=1),
                                       ticket("c", "queued", 2.0, at=2)])
        self.assertEqual(admitted, ["b"])
        self.assertEqual(reasons["c"], "要 2.0 核，heavy 已占 3.1 核（放行上限 4.8 核 = 容量 4 × 1.2）")

    def test_nothing_running_admits_head(self) -> None:
        """没有别的在跑就放行队首，哪怕它要整机（按独占）；它按真实核数（7）计入，后面的轻命令这一轮不放"""
        admitted, reasons = self.plan([ticket("x", "queued", 4.0, at=1, exclusive=True, cores=7.0),
                                       ticket("l", "queued", 0.5, at=2)])
        self.assertEqual(admitted, ["x"])
        self.assertIn("heavy 已占 7.0 核", reasons["l"])

    def test_exclusive_starts_when_others_use_little(self) -> None:
        """按独占的不再等在跑的全部结束：别的只占零头（≤ 容量 × 0.2）时就开跑，占得多了才等"""
        self.assertEqual(self.plan([ticket("a", "running", 0.5), ticket("x", "queued", 4.0, at=1, exclusive=True)])[0],
                         ["x"])
        admitted, reasons = self.plan([ticket("a", "running", 1.0), ticket("x", "queued", 4.0, at=1, exclusive=True)])
        self.assertEqual(admitted, [])
        self.assertIn("要 4.0 核，heavy 已占 1.0 核", reasons["x"])

    def test_small_commands_not_counted(self) -> None:
        """小命令在跑不计入占用：按独占的照样放行"""
        admitted, _ = self.plan([ticket("s", "running", 2.0, small=True), ticket("x", "queued", 4.0, at=1, exclusive=True)])
        self.assertEqual(admitted, ["x"])

    def test_starvation_blocks_later_arrivals(self) -> None:
        """排头没等太久时后来的能插空（backfill）；排头等了 ≥ 阈值，后来的没有历史墙钟（估不出能不能在它开跑前跑完）就不放，
        原因写防饿死"""
        tickets = [ticket("a", "running", 2.0), ticket("h", "queued", 3.0, at=0), ticket("l", "queued", 1.0, at=1)]
        admitted, _ = self.plan(tickets, now=10.0)
        self.assertEqual(admitted, ["l"])
        admitted, reasons = self.plan(tickets, now=200.0)
        self.assertEqual(admitted, [])
        self.assertIn("防饿死", reasons["l"])
        self.assertIn("cmd-h", reasons["l"])

    def test_memory_tight_only_alone(self) -> None:
        """内存紧张：有别的在跑就不放，什么都没跑时放；Linux 的可用内存比这条的峰值还小也不放"""
        tight = {"tight": True, "available_mb": None, "text": "内存压力严重"}
        admitted, reasons = self.plan([ticket("a", "running", 1.0), ticket("b", "queued", 1.0, at=1)], mem=tight)
        self.assertEqual(admitted, [])
        self.assertEqual(reasons["b"], "内存压力严重，等在跑的都结束")
        self.assertEqual(self.plan([ticket("b", "queued", 1.0, at=1)], mem=tight)[0], ["b"])
        low = {"tight": False, "available_mb": 4000.0, "text": "可用内存 3.9GB"}
        admitted, _ = self.plan([ticket("a", "running", 1.0), ticket("big", "queued", 1.0, at=1, mem_mb=6000),
                                 ticket("ok", "queued", 1.0, at=2, mem_mb=1000)], mem=low)
        self.assertEqual(admitted, ["ok"])


class MeasuredPlanTests(SchedTestCase):
    """按实测放行（底线：CPU 空着不能干等）：整机实测和票里的进程树实测都是造的，宽限期 1 秒（SchedTestCase
    的 config），默认"现在"是 100 秒、逻辑核 10 个、容量 4（放行上限 4.8）。"""

    def plan(self, tickets: list, busy: float = None, *, now: float = 100.0, starve: float = 120.0,
             mem: dict = NORMAL) -> tuple:
        return self.d.heavy_plan(tickets, 4.0, mem, now, starve, UNREADABLE if busy is None else cpu(busy))

    def test_idle_machine_admits_while_exclusive_runs(self) -> None:
        """"独占"的（全量 vitest，历史 7 核）在跑、进程树实测只占 0.3 核（收尾 / I/O 阶段），整机也空着：排队的 2 核马上放行。
        它刚放行（宽限期内、实测还没显出来）时按 7 核算，后来的先等"""
        x = ticket("x", "running", 4.0, exclusive=True, cores=7.0, started=0.0, cpu_now=0.3)
        light = ticket("l", "queued", 2.0, at=50.0)
        self.assertEqual(self.plan([x, light], busy=0.8)[0], ["l"])
        admitted, reasons = self.plan([dict(x, started_at=99.5), light], busy=0.8)
        self.assertEqual(admitted, [])
        self.assertIn("heavy 已占 7.0 核", reasons["l"])

    def test_measured_full_queues_until_it_frees(self) -> None:
        """实测满了才排队：在跑的进程树实测 4.5 核，再来 1 核的排队（原因写已占多少、上限）；它降到 1.5 核就放"""
        a = ticket("a", "running", 2.0, started=0.0, cpu_now=4.5)
        light = ticket("l", "queued", 1.0, at=50.0)
        admitted, reasons = self.plan([a, light], busy=5.0)
        self.assertEqual(admitted, [])
        self.assertEqual(reasons["l"], "要 1.0 核，heavy 已占 4.5 核（放行上限 4.8 核 = 容量 4 × 1.2）")
        self.assertEqual(self.plan([dict(a, cpu_now=1.5), light], busy=2.0)[0], ["l"])

    def test_machine_full_queues(self) -> None:
        """调度外的负载把整机占满（实测 9.6 / 10 核）：heavy 自己只占 0.5 核也不往上加；整机降到 8.5 核就放；
        没有 heavy 在跑时照样放一条（调度外的负载等下去不一定会降）"""
        a = ticket("a", "running", 1.0, started=0.0, cpu_now=0.5)
        light = ticket("l", "queued", 1.0, at=50.0)
        admitted, reasons = self.plan([a, light], busy=9.6)
        self.assertEqual(admitted, [])
        self.assertEqual(reasons["l"], "要 1.0 核，整机实测已忙 9.6 / 10 核")
        self.assertEqual(self.plan([a, light], busy=8.5)[0], ["l"])
        self.assertEqual(self.plan([light], busy=9.6)[0], ["l"])

    def test_unknown_command_by_measurement(self) -> None:
        """没有历史的先按 1 核（不再独占）：刚放行时别的 1 核照样进；跑起来实测 3.9 核时别的 1 核排队"""
        demand = self.d.heavy_demand({"keys": {}, "exact": {}}, {"key": "k", "family": "f", "exact_key": "e"}, 4.0)
        self.assertEqual((demand["reserve"], demand["exclusive"], demand["source"]),
                         (1.0, False, "没有历史，先按 1 核，跑起来按实测"))
        light = ticket("l", "queued", 1.0, at=50.0)
        new = ticket("u", "running", 1.0, started=99.5)
        self.assertEqual(self.plan([new, light], busy=0.5)[0], ["l"])
        admitted, reasons = self.plan([dict(new, started_at=0.0, cpu_now=3.9, cpu_at=100.0), light], busy=4.4)
        self.assertEqual(admitted, [])
        self.assertIn("heavy 已占 3.9 核", reasons["l"])

    def test_no_ps_capped_by_machine(self) -> None:
        """codex 沙箱里没有 ps（票里没有进程树实测）：按预留 / 真实核数算，但不超过整机实测——"独占"的在跑、整机只忙 1 核时
        轻命令照样进；整机也读不到时才退回按预留排队"""
        x = ticket("x", "running", 4.0, exclusive=True, cores=7.0, started=0.0)
        light = ticket("l", "queued", 2.0, at=50.0)
        self.assertEqual(self.plan([x, light], busy=1.0)[0], ["l"])
        admitted, reasons = self.plan([x, light])
        self.assertEqual(admitted, [])
        self.assertIn("heavy 已占 7.0 核", reasons["l"])

    def test_starvation_backfills_only_what_finishes_before_head(self) -> None:
        """排头要 3 核、等了 150 秒；在跑的实测 3.5 核、按历史 300 秒时结束：后来的装得下、并且 300 秒前跑完的放（60 秒的），
        跑不完的（200 秒）、没有历史的不放。这时实测空着 1.1 核（< 排头要的 3 核）还有命令在排队，是允许的唯一情形：
        空出来的不够排头，把它们放进来就是把排头往后推。排头没等满阈值时照常插空"""
        a = ticket("a", "running", 3.5, started=0.0, cpu_now=3.5, cpu_at=150.0, wall=300.0)
        head = ticket("h", "queued", 3.0, at=0.0)
        quick = ticket("quick", "queued", 0.2, at=1.0, wall=60.0)
        slow = ticket("slow", "queued", 0.2, at=2.0, wall=200.0)
        unknown = ticket("unknown", "queued", 0.2, at=3.0)
        admitted, reasons = self.plan([a, head, quick, slow, unknown], busy=4.0, now=150.0)
        self.assertEqual(admitted, ["quick"])
        self.assertIn("cmd-h", reasons["slow"])
        self.assertIn("它开跑前跑不完", reasons["slow"])
        self.assertIn("估不出", reasons["unknown"])
        self.assertIn("heavy 已占 3.5 核", reasons["h"])  # 排头自己是真的装不下
        self.assertEqual(self.plan([a, head, quick, slow, unknown], busy=4.0, now=100.0)[0], ["quick", "slow", "unknown"])


class CpuMeterTests(SchedTestCase):
    """整机 CPU 实测 heavy_cpu（计数是造的、时间注入）、进程树 CPU 的算法、ps 时间列的解析；本机真的读得到。"""

    def setUp(self) -> None:
        super().setUp()
        os.environ.pop("DROVER_HEAVY_CPU_FILE")  # load_drover 换进了临时环境；这一组测真的读法
        self.d.HEAVY_CPU_SAMPLES.clear()

    def test_window(self) -> None:
        """两次采样隔 ≥ 0.5 秒才算数（之前是 warming）；忙 = 忙的计数增量 ÷ 全部增量 × 逻辑核数；只看最近 2 秒；
        读不到时写明、放行退回按各条的实测 / 预留；macOS 的 32 位计数回绕"""
        counters = {0.0: ((0, 0, 0), (0,), 4), 0.25: ((10, 0, 0), (10,), 4), 1.0: ((50, 0, 0), (50,), 4),
                    3.0: ((290, 0, 0), (60,), 4)}
        self.d.heavy_cpu_counters = lambda: counters[now]
        for now, want in ((0.0, None), (0.25, None), (1.0, 2.0), (3.0, 3.84)):
            got = self.d.heavy_cpu(now)
            self.assertEqual(got["busy"], want, now)
        self.assertEqual(got["text"], "整机实测忙 3.8 / 4 核（最近 2.0 秒）")  # t=0、0.25 的已经不在窗口里
        self.d.heavy_cpu_counters = lambda: None
        self.assertIn("读不到", self.d.heavy_cpu(4.0)["text"])
        self.assertEqual(self.d.heavy_tick_delta(2 ** 32 - 10, 5), 15)

    def test_fake_file(self) -> None:
        """DROVER_HEAVY_CPU_FILE（自测用）：文件里的核数就是整机实测，文件不在就是读不到"""
        os.environ["DROVER_HEAVY_CPU_FILE"] = str(self.cpu_file)
        self.set_cpu_busy(2.5)
        self.assertEqual(self.d.heavy_cpu()["busy"], 2.5)
        self.set_cpu_busy(None)
        self.assertIsNone(self.d.heavy_cpu()["busy"])

    def test_tree_rate_and_ps_time(self) -> None:
        """进程树占几核：两次都在的按差值、新起来的按全部、退出的和 pid 被复用的不算；ps 的 time 列（macOS 带百分秒、分钟可以过
        60，Linux 是 天-时:分:秒）"""
        old = {1: 10.0, 2: 5.0, 3: 1.0, 5: 9.0}
        new = {1: 12.0, 2: 5.5, 4: 0.5, 5: 0.2}
        self.assertEqual(self.d.tree_cpu_rate(old, new, 2.0), 1.5)
        for text, seconds in (("0:00.71", 0.71), ("7626:23.99", 457583.99), ("1:02:03.45", 3723.45), ("2-01:00:00", 176400)):
            self.assertAlmostEqual(self.d.ps_cpu_seconds(text), seconds, places=2)
        self.assertIsNone(self.d.ps_cpu_seconds("abc"))

    def test_real_counters_readable(self) -> None:
        """本机（macOS / Linux）真的读得到整机计数，而且是新鲜的：隔 0.1 秒两次读不一样（macOS 以前用的 host_statistics
        对普通进程全机限速、每秒只给 2～10 次新数据，几个进程一起读时常常拿到同一份缓存，-j 10 跑全量时这里隔 0.6 秒还算不出
        忙几核）；隔 0.6 秒算出来的忙在 0 到逻辑核数之间（不看具体值：那是本机负载）"""
        if sys.platform not in ("darwin", "linux"):
            self.skipTest("只有 macOS / Linux 读整机 CPU")
        first = self.d.heavy_cpu_counters()
        self.assertIsNotNone(first)
        time.sleep(0.1)
        self.assertNotEqual(self.d.heavy_cpu_counters(), first)
        self.assertTrue(self.d.heavy_cpu()["warming"])
        time.sleep(0.6)
        got = self.d.heavy_cpu()
        self.assertIsNotNone(got["busy"], got)
        self.assertTrue(0 <= got["busy"] <= got["logical"], got)


class DemandAndShapeTests(SchedTestCase):
    def test_demand_from_history(self) -> None:
        """需求：这条命令自己（exact）的最近几次取最大值；没有就用命令形状的，再没有用同程序（family）的；都没有先按 1 核。
        实测 ≥ 容量按独占；历史上最长也在 heavy_small_seconds（默认 10 秒）内跑完的是小命令"""
        shape = {"key": "r||tsc --noEmit", "family": "r||tsc", "exact_key": "r||tsc --noEmit -p a"}
        runs = [{"wall": 20.0, "cpu": 20.0}, {"wall": 26.0, "cpu": 36.4}]
        doc = {"keys": {"r||tsc --noEmit": {"family": "r||tsc", "runs": runs}}, "exact": {}}
        same_shape = self.d.heavy_demand(doc, shape, 4.0)
        self.assertEqual((same_shape["cores"], same_shape["reserve"], same_shape["exclusive"], same_shape["small"],
                          same_shape["wall"]), (1.4, 1.4, False, False, 26.0))
        self.assertEqual(same_shape["source"], "同类命令实测 2 次")
        mine = dict(doc, exact={"r||tsc --noEmit -p a": {"runs": [{"wall": 2.0, "cpu": 1.0}]}})
        own = self.d.heavy_demand(mine, shape, 4.0)  # 自己的实测优先于同形状的
        self.assertEqual((own["reserve"], own["small"], own["source"]), (0.5, True, "这条命令实测 1 次"))
        family = self.d.heavy_demand(doc, {"key": "r||tsc -p x", "family": "r||tsc", "exact_key": "r||tsc -p x"}, 4.0)
        self.assertEqual((family["reserve"], family["source"]), (1.4, "同程序实测 2 次"))
        unknown = self.d.heavy_demand(doc, {"key": "r||vitest run", "family": "r||vitest run",
                                            "exact_key": "r||vitest run"}, 4.0)
        self.assertEqual((unknown["exclusive"], unknown["reserve"], unknown["source"]),
                         (False, 1.0, "没有历史，先按 1 核，跑起来按实测"))
        k = {"key": "k", "family": "f", "exact_key": "k"}
        big = self.d.heavy_demand({"keys": {"k": {"family": "f", "runs": [{"wall": 320.0, "cpu": 2240.0}]}}, "exact": {}},
                                  k, 4.0)
        self.assertEqual((big["exclusive"], big["reserve"]), (True, 4.0))
        self.assertIn("7.0 核 ≥ 容量", big["source"])
        small = self.d.heavy_demand({"keys": {"k": {"family": "f", "runs": [{"wall": 1.2, "cpu": 0.6}]}}, "exact": {}},
                                    k, 4.0)
        self.assertTrue(small["small"])

    def test_demand_prefers_same_command(self) -> None:
        """同形状里有个重的（35.8 秒），轻文件自己单独跑过 2 次（3 秒多）：按它自己的实测，是小命令、建票时就在跑（不排队）；
        没跑过的同形状新文件照旧按形状估（不是小命令、排队）；别的形状按同程序估"""
        repo = self.work / "app"
        self.init_repo(repo, {"selftest/t.py": "x"})
        self.d.HEAVY_DIR.mkdir(parents=True)

        def argv(name: str) -> list:
            return ["python3", "-m", "unittest", "discover", "-s", "selftest", "-p", name]

        for name, wall, cpu, end in (("test_big.py", 35.8, 26.9, 1000), ("test_light.py", 3.1, 2.8, 2000),
                                     ("test_light.py", 3.3, 3.0, 3000)):
            t = {**self.d.heavy_shape(argv(name), str(repo)), "id": name, "reserve": 1.0, "small": False}
            self.d.heavy_history_add(t, {"seconds": wall, "cpu_seconds": cpu, "mem_mb": 100, "exit_code": 0}, [], 4.0,
                                     now=end)
        light = self.d.heavy_enqueue(argv("test_light.py"), str(repo), 4.0)
        self.addCleanup(self.d.heavy_drop, light)
        new = self.d.heavy_enqueue(argv("test_new.py"), str(repo), 4.0)
        self.addCleanup(self.d.heavy_drop, new)
        self.assertEqual((light["data"]["small"], light["data"]["state"], light["data"]["source"]),
                         (True, "running", "这条命令实测 2 次"))
        self.assertEqual((new["data"]["small"], new["data"]["state"], new["data"]["source"], new["data"]["wall"]),
                         (False, "queued", "同类命令实测 3 次", 35.8))
        other = self.d.heavy_demand(self.d.heavy_doc(), self.d.heavy_shape(
            ["python3", "-m", "unittest", "discover", "-s", "tests"], str(repo)), 4.0)
        self.assertEqual(other["source"], "同程序实测 3 次")

    def test_shape_keys(self) -> None:
        """命令形状：任务书点名的 7 条命令的键；同一仓库的不同 worktree 相同、不同仓库不同；-Dk=值、提交、文件个数分档"""
        files = {"src/a.test.ts": "x", "src/b.test.ts": "x", "tests/test_x.py": "x", "selftest/t.py": "x",
                 "scripts/i18n/verify-locales.mjs": "x"}
        repo = self.work / "app"
        self.init_repo(repo, files)
        other_wt = self.tmp / "wt-app"
        self.git(repo, "worktree", "add", "-q", "--detach", str(other_wt))
        other_repo = self.work / "other"
        self.init_repo(other_repo, files)
        cases = [
            (["bunx", "vitest", "run", "src/a.test.ts", "src/b.test.ts"], "bunx vitest run <2-4 个文件>", "bunx vitest run"),
            (["bunx", "vitest", "run"], "bunx vitest run", "bunx vitest run"),
            (["./.venv/bin/python", "-m", "pytest", "tests/test_x.py", "-q"], "python -m pytest -q <1 个文件>",
             "python -m pytest"),
            (["env", "JAVA_HOME=/x", "mvn", "-q", "-o", "test"], "mvn -q -o test", "mvn"),
            (["python3", "-m", "unittest", "discover", "-s", "selftest"], "python3 -m unittest discover -s selftest",
             "python3 -m unittest discover"),
            (["bun", "run", "build"], "bun run build", "bun run build"),
            (["bun", "scripts/i18n/verify-locales.mjs"], "bun scripts/i18n/verify-locales.mjs",
             "bun scripts/i18n/verify-locales.mjs"),
            (["JAVA_HOME=/y", "mvn", "-q", "test", "-Dtest=FooTest"], "mvn -q test -Dtest=*", "mvn"),
            (["bunx", "vitest", "run", "--changed", "8bece1ccfa23"], "bunx vitest run --changed <rev>", "bunx vitest run"),
            (["pytest", "tests/test_x.py::test_a"], "pytest <1 个文件>", "pytest"),
            (["eslint", *[f"src/f{i}.ts" for i in range(5)]], "eslint <5-16 个文件>", "eslint"),
        ]
        for argv, shape, family in cases:
            with self.subTest(argv=argv):
                here = self.d.heavy_shape(argv, str(repo))
                self.assertEqual(here["shape"], shape)
                self.assertTrue(here["family"].endswith("|" + family), here["family"])
                self.assertEqual(here["repo"], "app")
                self.assertEqual(self.d.heavy_shape(argv, str(other_wt))["key"], here["key"])  # 同仓库另一个 worktree
                self.assertNotEqual(self.d.heavy_shape(argv, str(other_repo))["key"], here["key"])  # 别的仓库
        sub = self.d.heavy_shape(["bun", "run", "build"], str(repo / "src"))
        self.assertEqual(sub["repo"], "app/src")
        self.assertNotEqual(sub["key"], self.d.heavy_shape(["bun", "run", "build"], str(repo))["key"])  # 子目录分开
        # 同一条命令（exact）：参数不同的同形状命令不是同一条；路径按 worktree 规范化，另一个 worktree 的同一条命令相同
        unit = ["python3", "-m", "unittest", "discover", "-s", "selftest", "-p"]
        a, b = (self.d.heavy_shape(unit + [name], str(repo)) for name in ("test_a.py", "test_b.py"))
        self.assertEqual((a["shape"], a["key"]), ("python3 -m unittest discover -s selftest -p <1 个文件>", b["key"]))
        self.assertEqual(a["exact"], "python3 -m unittest discover -s selftest -p test_a.py")
        self.assertNotEqual(a["exact_key"], b["exact_key"])
        here, there = (self.d.heavy_shape(["bunx", "vitest", "run", str(wt / "src/a.test.ts"), "-t", "adds"], str(wt))
                       for wt in (repo, other_wt))
        self.assertEqual((here["exact"], here["exact_key"]), ("bunx vitest run src/a.test.ts -t adds", there["exact_key"]))
        self.assertNotEqual(self.d.heavy_shape(["bunx", "vitest", "run", "src/b.test.ts", "-t", "adds"], str(repo))
                            ["exact_key"], here["exact_key"])
        # 带通配符的值是模式、不是一个文件（可能就是全量）：按普通的词留在形状里，不和点名单个文件的归成一类
        glob = self.d.heavy_shape(unit + ["test*.py"], str(repo))
        self.assertEqual(glob["shape"], "python3 -m unittest discover -s selftest -p test*.py")
        self.assertEqual(glob["exact"], "python3 -m unittest discover -s selftest -p 'test*.py'")

    def test_history_capped(self) -> None:
        """命令形状、同一条命令各只留最近 5 次；条数超过上限时按最近一次使用留新的"""
        self.d.HEAVY_DIR.mkdir(parents=True)
        res = {"seconds": 1.0, "cpu_seconds": 1.0, "mem_mb": 10, "exit_code": 0}
        t = {"key": "k0", "family": "f", "shape": "s", "repo": "r", "reserve": 1.0, "small": True, "exact": "c0",
             "exact_key": "c0"}
        for _ in range(7):
            self.d.heavy_history_add(t, res, [], 4.0)
        self.assertEqual(len(self.history()["k0"]["runs"]), 5)
        self.assertEqual(len(self.d.heavy_doc()["exact"]["c0"]["runs"]), 5)
        self.d.HEAVY_HISTORY_KEYS = 3
        for i in range(1, 5):
            self.d.heavy_history_add(dict(t, key=f"k{i}", exact_key=f"c{i}"), res, [], 4.0)
        self.assertEqual(sorted(self.history()), ["k2", "k3", "k4"])
        self.assertEqual(sorted(self.d.heavy_doc()["exact"]), ["c2", "c3", "c4"])


class HeavySchedCliTests(SchedTestCase):
    """drover heavy 端到端（容量 4 核、宽限期 1 秒，历史直接写进 history.json；整机实测是假采样文件，默认 0 核，
    进程树实测是真的 ps——hold.py 几乎不占 CPU，不对它的数值下断言）。"""

    def test_light_commands_run_together(self) -> None:
        """两条各 1.5 核（合计 3 ≤ 4）：同时在跑，第二条不排队"""
        for name in ("a", "b"):
            self.seed(self.hold_argv(name), self.tmp, cores=1.5)
        a, b = self.start("a"), self.start("b")
        self.assertTrue(wait_for(lambda: self.started("a") and self.started("b")), self.stderr("b"))
        self.assertNotIn("排队中", self.stderr("a") + self.stderr("b"))
        self.release("a", a)
        self.release("b", b)

    def test_full_machine_queues_and_status(self) -> None:
        """整机实测满了（假采样：忙到逻辑核数）：3 核的在跑时再来一条 2 核的排队，打印要多少、整机实测忙多少、heavy 占多少 /
        容量、谁在占、为什么等；--status 看得到实测和排队的。整机空出来它马上开跑，不等前一条结束（hold.py 几乎不占 CPU，
        过了宽限期就按实测算）。跑完票都退掉，实测记进历史"""
        logical = os.cpu_count() or 1
        self.seed(self.hold_argv("a"), self.tmp, cores=3.0)
        self.seed(self.hold_argv("b"), self.tmp, cores=2.0)
        self.set_cpu_busy(logical)
        a = self.start("a")
        self.assertTrue(wait_for(lambda: self.started("a")))
        b = self.start("b")
        self.assertTrue(wait_for(lambda: "整机实测已忙" in self.stderr("b")), self.stderr("b"))  # a 过了宽限期以后
        self.assertFalse(self.started("b"))
        err = self.stderr("b")
        self.assertIn("排队中：这条要 2.0 核（这条命令实测 1 次）", err)
        self.assertIn(f"整机实测忙 {logical:.1f} / {logical} 核", err)
        self.assertIn("/ 容量 4 核", err)
        self.assertIn(f"在跑：pid {a.pid} ", err)
        self.assertIn("hold.py a", err)
        self.assertIn(f"要 2.0 核，整机实测已忙 {logical:.1f} / {logical} 核", err)
        status = self.drover("heavy", "--status").stdout
        self.assertIn("容量 4 核（config 的 heavy_cpu_capacity，固定、不校准）", status)
        self.assertIn(f"实测：整机实测忙 {logical:.1f} / {logical} 核；heavy 占 ", status)
        self.assertIn("放行：heavy 占用 + 需求 ≤ 容量 × 1.2 = 4.8 核", status)
        self.assertIn("在跑 1 条：", status)
        self.assertIn(f"3.0 核（这条命令实测 1 次）  pid {a.pid} ", status)
        self.assertIn("排队 1 条", status)
        self.assertIn(f"1. 要 2.0 核（这条命令实测 1 次）  pid {b.pid} ", status)
        self.assertIn("整机实测已忙", status)
        freed = time.time()
        self.set_cpu_busy(0.5)
        self.assertTrue(wait_for(lambda: self.started("b")), self.stderr("b"))
        self.assertGreaterEqual(float((self.marks / "b.start").read_text()), freed)
        self.assertFalse((self.marks / "a.end").exists())  # 前一条还在跑
        self.release("a", a)
        self.release("b", b)
        self.assertIn("开始执行", self.stderr("b"))
        self.assertEqual(self.tickets(), [])
        self.assertEqual([len(e["runs"]) for e in self.history().values()], [2, 2])
        self.assertIn("在跑 0 条", self.drover("heavy", "--status").stdout)

    def ticket_of(self, name: str) -> dict:
        """hold.py <名字> 那条命令的票（没有、或读到属主写了一半的，返回 {}）。"""
        for path in self.tickets():
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if f"hold.py {name} " in data.get("command", ""):
                return data
        return {}

    def test_unknown_command_not_exclusive(self) -> None:
        """没有历史的不再按独占（底线同上：CPU 空着不能干等）：先按 1 核，机器有余量就直接跑、不等在跑的；跑起来后属主把进程树的实测
        写进票（cpu_now），--status 显示；它跑着时别的轻命令也照样进，小命令不排队"""
        for name in ("light", "light2"):
            self.seed(self.hold_argv(name), self.tmp, cores=0.5)
        light = self.start("light")
        self.assertTrue(wait_for(lambda: self.started("light")))
        new = self.start("new")
        self.assertTrue(wait_for(lambda: self.started("new")), self.stderr("new"))
        self.assertNotIn("排队中", self.stderr("new"))
        data: dict = {}
        self.assertTrue(wait_for(lambda: data.update(self.ticket_of("new")) or "cpu_now" in data), data)
        self.assertEqual((data["exclusive"], data["reserve"], data["source"]),
                         (False, 1.0, "没有历史，先按 1 核，跑起来按实测"))
        self.assertRegex(self.drover("heavy", "--status").stdout, r"hold\.py new .*实测 [\d.]+ 核")
        light2 = self.start("light2")
        self.assertTrue(wait_for(lambda: self.started("light2")), self.stderr("light2"))
        self.seed(self.hold_argv("tiny"), self.tmp, cores=2.0, wall=1.0)  # 小命令：历史上 1 秒跑完
        tiny = self.start("tiny", hold=False)
        self.assertEqual(tiny.wait(30), 0)
        self.assertNotIn("排队中", self.stderr("tiny"))
        for name, p in (("light", light), ("new", new), ("light2", light2)):
            self.release(name, p)

    def test_sandbox_fallback(self) -> None:
        """codex 沙箱的退化：ps 不让执行（PATH 里换成一直失败的假 ps）→ 票里没有进程树实测，"独占"的（历史 7 核）按 7 核算；
        整机也读不到（--status 写明）就退回按预留，轻命令排队；整机读得到、只忙 0.5 核时，整机实测把它压回去，轻命令开跑"""
        ps = self.fakebin / "ps"
        ps.write_text("#!/bin/sh\nexit 1\n")
        ps.chmod(0o755)
        self.seed(self.hold_argv("x"), self.tmp, cores=7.0)
        self.seed(self.hold_argv("l"), self.tmp, cores=0.5)
        self.set_cpu_busy(None)
        x = self.start("x")
        self.assertTrue(wait_for(lambda: self.started("x")))
        light = self.start("l")
        self.assertTrue(wait_for(lambda: "heavy 已占 7.0 核" in self.stderr("l")), self.stderr("l"))
        self.assertIn("整机 CPU 读不到", self.drover("heavy", "--status").stdout)
        self.assertNotIn("cpu_now", self.ticket_of("x"))
        self.set_cpu_busy(0.5)
        self.assertTrue(wait_for(lambda: self.started("l")), self.stderr("l"))
        self.assertFalse((self.marks / "x.end").exists())
        self.release("l", light)
        self.release("x", x)

    def test_killed_holder_is_reclaimed(self) -> None:
        """持有者被 SIGKILL（来不及退票）：它的票随进程的 flock 一起作废，排队的那条（整机实测满了在等）接着开始"""
        self.set_cpu_busy(os.cpu_count() or 1)
        holder = self.start("holder")
        self.assertTrue(wait_for(lambda: self.started("holder")))
        self.seed(self.hold_argv("next"), self.tmp, cores=0.5)
        nxt = self.start("next", hold=False)
        self.assertTrue(wait_for(lambda: "排队中" in self.stderr("next")))
        os.kill(holder.pid, signal.SIGKILL)
        holder.wait(10)
        self.assertTrue(wait_for(lambda: self.started("next")), self.stderr("next"))
        self.assertEqual(nxt.wait(30), 0)
        self.assertEqual(self.tickets(), [])

    def test_sigterm_ends_child_tree(self) -> None:
        """heavy 收到 SIGTERM：子进程和孙进程一起结束，退票，退出码 143"""
        p = self.start("tree", child=True)
        self.assertTrue(wait_for(lambda: (self.marks / "tree.gpid").exists()))
        gpid = int((self.marks / "tree.gpid").read_text())
        self.assertTrue(alive(gpid))
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(30), 143)
        self.assertTrue(wait_for(lambda: not alive(gpid)), "孙进程还活着")
        self.assertFalse((self.marks / "tree.end").exists())  # hold.py 是被信号结束的，没走到正常结束
        self.assertEqual(self.tickets(), [])

    def test_measures_tree_cpu(self) -> None:
        """跑完记实测：CPU 时间含孙进程，墙钟、退出码、内存峰值都有；失败的（退出码非 0）也记。孙进程按 CPU 时间空转到
        0.4 秒（time.process_time），机器忙、CPU 被抢时也烧够；按墙钟空转的话被抢了就烧不到 0.3 秒，偶发失败"""
        burn = ("import subprocess, sys; subprocess.run([sys.executable, '-c', "
                "'import time\\nt = time.process_time()\\nwhile time.process_time() - t < 0.4: pass']); sys.exit(3)")
        self.drover("heavy", "--", sys.executable, "-c", burn, rc=3)
        (entry,) = self.history().values()
        (run,) = entry["runs"]
        self.assertGreaterEqual(run["cpu"], 0.3)
        self.assertGreaterEqual(run["wall"], 0.3)
        self.assertEqual(run["exit"], 3)
        self.assertGreater(run["mem_mb"], 0)


class AcceptSchedTests(SchedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.wt = self.work / "app"
        self.base = self.init_repo(self.wt, {"README.md": "x\n"})
        self.rdv = self.tmp / "rdv.py"
        self.rdv.write_text(RENDEZVOUS)

    def rdv_cmd(self, name: str, other: str) -> str:
        argv = [sys.executable, str(self.rdv), name, other, str(self.marks)]
        self.seed(argv, self.wt, cores=0.5)  # 轻命令（不是小命令）：要经调度放行
        return shlex.join(argv)

    def mark(self, name: str, kind: str) -> float:
        return float((self.marks / f"{name}.{kind}").read_text())

    def test_verifies_run_together_in_order(self) -> None:
        """两条轻 --verify：时间上重叠（互相等到对方开始），结果按传入顺序"""
        a, b = self.rdv_cmd("a", "b"), self.rdv_cmd("b", "a")
        res = self.drover_json("accept", "--wt", str(self.wt), "--base", self.base, "--no-graph", "--verify", a,
                               "--verify", b)
        self.assertEqual([v["command"] for v in res["verify"]], [a, b])
        self.assertEqual([v["summary_line"] for v in res["verify"]], ["done a", "done b"])
        self.assertFalse(res["verify_failed"])
        self.assertLess(self.mark("a", "start"), self.mark("b", "end"))
        self.assertLess(self.mark("b", "start"), self.mark("a", "end"))

    def test_serial_runs_one_by_one(self) -> None:
        """--serial：逐条跑，第二条在第一条结束之后才开始，结果顺序不变"""
        (self.marks / "wait").write_text("1")  # 逐条跑时第一条等不到第二条，等 1 秒就往下
        a, b = self.rdv_cmd("a", "b"), self.rdv_cmd("b", "a")
        res = self.drover_json("accept", "--wt", str(self.wt), "--base", self.base, "--no-graph", "--serial",
                               "--verify", a, "--verify", b)
        self.assertEqual([v["command"] for v in res["verify"]], [a, b])
        self.assertGreaterEqual(self.mark("b", "start"), self.mark("a", "end"))

    def test_no_lock_skips_scheduler(self) -> None:
        """--no-lock：不进调度，不建调度目录、不记实测"""
        cmd = shlex.join([sys.executable, "-c", "pass"])
        res = self.drover_json("accept", "--wt", str(self.wt), "--base", self.base, "--no-graph", "--no-lock",
                               "--verify", cmd)
        self.assertTrue(res["verify"][0]["ok"])
        self.assertFalse(self.heavy.exists())

    def test_graph_build_scheduled(self) -> None:
        """建图（code-review-graph build）进调度：记下了它的实测"""
        bindir = self.tmp / "graphbin"
        bindir.mkdir()
        graph = bindir / "code-review-graph"
        graph.write_text(f"#!{sys.executable}\nimport sys\nif sys.argv[1:2] == ['detect-changes']:\n"
                         "    print('Analyzed 1 changed file')\n")
        graph.chmod(0o755)
        self.env["PATH"] = str(bindir) + os.pathsep + self.env["PATH"]
        res = self.drover_json("accept", "--wt", str(self.wt), "--base", self.base)
        self.assertTrue(res["graph"]["ok"], res["graph"])
        self.assertIn("code-review-graph build --repo .", [e["shape"] for e in self.history().values()])

    def test_baseline_prep_outside_reservation(self) -> None:
        """--verify-baseline：建基线 worktree、复制本地文件、装依赖时不占预留（没有任何排队票），在基线上跑命令那一步进调度
        （记了实测）；vitest 超时复核也进调度"""
        fail_a = {"file": "src/a.test.ts", "test": "math > adds", "kind": "assert"}
        self.base = self.commit(self.wt, "tests", {"src/a.test.ts": "// x\n",
                                                   "fake-vitest.json": json.dumps({"failures": [fail_a]})})
        seen = []
        for name in ("copy_local_files", "cmd_deps"):
            original = getattr(self.d, name)

            def spy(*args, _original=original, _name=name, **kw):
                seen.append((_name, [p.name for p in self.d.HEAVY_DIR.glob("run-*.json")]))
                return _original(*args, **kw)
            setattr(self.d, name, spy)
        entry = self.d.run_verify(str(self.wt), "vitest run", 60, baseline=self.base)
        self.assertTrue(entry["ok"], entry)
        self.assertIn("没有新增", entry["note"])
        self.assertEqual(seen, [("copy_local_files", []), ("cmd_deps", [])])
        shapes = sorted(e["shape"] for e in self.history().values())
        self.assertEqual(shapes, ["vitest run", "vitest run <1 个文件>"])  # 这边的整条 + 基线上只跑失败的文件
        # 超时复核：失败全是超时 → 单独重跑失败文件，也经调度
        self.write_spec(self.wt, "vitest", {"failures": [dict(fail_a, kind="timeout")]})
        entry = self.d.run_verify(str(self.wt), "vitest run --environment node", 60)
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["flaky_timeouts"], ["src/a.test.ts"])
        self.assertIn("vitest run --environment node <1 个文件>", [e["shape"] for e in self.history().values()])
        self.assertEqual(self.tickets(), [])


class CalibrationTests(SchedTestCase):
    """容量自动校准：硬件值写死成 4、逻辑核数 5（不看本机），样本和时间都是造的。"""

    def setUp(self) -> None:
        super().setUp()
        path = self.state / "config.json"
        cfg = json.loads(path.read_text())
        cfg.pop("heavy_cpu_capacity")  # 不固定容量才校准
        path.write_text(json.dumps(cfg))
        self.d = self.load_drover()
        self.real_hardware = self.d.heavy_hardware()[0]
        self.d.heavy_hardware = lambda: (4.0, "测试硬件")
        self.d.heavy_logical_cores = lambda: 5

    def feed(self, *samples: tuple, cal: dict = None) -> dict:
        cal = cal or {}
        for slowdown, load in samples:
            cal = self.d.heavy_calibrate(cal, {"at": 0, "shape": "s", "repo": "r", "slowdown": slowdown, "load": load},
                                         4.0, 5)
        return cal

    def test_slow_lowers_one_step(self) -> None:
        """同时跑明显变慢（≥ 1.5 倍、负载 ≥ 容量一半）连续 2 个 → 降一核；1 个不动；负载很低时的变慢不算数"""
        self.assertEqual(self.feed((2.0, 3.5))["capacity"], 4.0)
        cal = self.feed((2.0, 3.5), (1.8, 3.0))
        self.assertEqual(cal["capacity"], 3.0)
        self.assertIn("明显变慢", cal["changes"][-1]["reason"])
        self.assertEqual(self.feed((2.0, 1.0), (2.0, 1.0), (2.0, 1.0))["capacity"], 4.0)
        self.assertEqual(self.feed(*[(3.0, 4.0)] * 10)["capacity"], 1.0)  # 不低于 1，一次一步

    def test_fast_raises_capped_at_logical_cores(self) -> None:
        """接近容量（负载 ≥ 容量 − 1）几乎不变慢（≤ 1.2 倍）连续 3 个 → 加一核，不超过逻辑核数；中间夹着明显变慢的重新攒，
        不算数的（负载低）不打断"""
        self.assertEqual(self.feed((1.1, 3.5), (1.0, 3.2))["capacity"], 4.0)  # 样本不够不动
        cal = self.feed((1.1, 3.5), (1.0, 3.2), (1.1, 1.0), (1.2, 3.6))
        self.assertEqual(cal["capacity"], 5.0)
        self.assertEqual([s["verdict"] for s in cal["samples"]], ["fast", "fast", "neutral", "fast"])
        self.assertEqual(self.feed(*[(1.0, 4.5)] * 3, cal=cal)["capacity"], 5.0)  # 已到逻辑核数
        self.assertEqual(self.feed((1.1, 3.5), (1.1, 3.5), (1.6, 3.5), (1.1, 3.5))["capacity"], 4.0)

    def test_backoff_after_coming_down(self) -> None:
        """从 5 降回 4 以后，再升到 5 要 2 倍的连续样本（6 个）：3 个不动，6 个才升；fails 记下 5 降过 1 次"""
        cal = self.feed(*[(1.1, 3.5)] * 3)
        cal = self.feed((2.0, 4.5), (2.0, 4.5), cal=cal)
        self.assertEqual((cal["capacity"], cal["fails"]), (4.0, {"5": 1}))
        cal = self.feed(*[(1.1, 3.5)] * 3, cal=cal)
        self.assertEqual(cal["capacity"], 4.0)
        cal = self.feed(*[(1.1, 3.5)] * 3, cal=cal)
        self.assertEqual(cal["capacity"], 5.0)
        self.assertIn("连续 6 个", cal["changes"][-1]["reason"])

    def test_hardware_change_restarts(self) -> None:
        """校准状态记的硬件值和现在的不一样（换了机器）→ 从现在的硬件值重新起步"""
        cal = self.d.heavy_calibrate({"hardware": 8.0, "capacity": 2.0},
                                     {"at": 0, "shape": "s", "repo": "r", "slowdown": 1.3, "load": 3.0}, 4.0, 5)
        self.assertEqual((cal["hardware"], cal["capacity"]), (4.0, 4.0))

    def add(self, key: str, wall: float, end: float, tickets: list = (), small: bool = False, exit_code: int = 0,
            cmd: str = None) -> dict:
        """记一次跑完的实测（时间是造的）：命令形状 key，同一条命令 cmd（不给就和 key 相同）。返回整个调度历史。"""
        cmd = cmd or key
        t = {"id": cmd, "key": key, "family": key, "shape": key, "repo": "r", "reserve": 1.0, "small": small,
             "exact": cmd, "exact_key": "r|" + cmd}
        self.d.heavy_history_add(t, {"seconds": wall, "cpu_seconds": wall, "mem_mb": 1, "exit_code": exit_code},
                                 list(tickets), 4.0, now=end)
        return self.d.heavy_doc()

    def test_samples_from_history(self) -> None:
        """同一条命令单独跑的墙钟当基准（取中位数）；和别的非小命令重叠的跑完成为样本：变慢 = 墙钟 ÷ 基准，负载按时间加权、
        含自己，样本带命令和基准。小命令不当样本、不当基准；退出码非 0 不和退出码 0 的基准比"""
        self.d.HEAVY_DIR.mkdir(parents=True)
        self.add("a", 10, 100)
        doc = self.add("a", 10, 200)
        self.assertEqual(doc["exact"]["r|a"]["solo"], [{"at": 100, "wall": 10, "ok": True}, {"at": 200, "wall": 10, "ok": True}])
        self.assertNotIn("solo", doc["keys"]["a"])  # 基准不按命令形状存
        self.assertNotIn("samples", doc["calibration"])
        b = {"id": "b", "state": "running", "started_at": 290.0, "reserve": 2.0}  # 2 核，后 10 秒和它重叠
        doc = self.add("a", 20, 300, [b])
        (sample,) = doc["calibration"]["samples"]
        self.assertEqual((sample["slowdown"], sample["load"], sample["verdict"]), (2.0, 2.0, "slow"))
        self.assertEqual((sample["command"], sample["base"], sample["base_runs"]), ("a", 10, 2))
        doc = self.add("a", 20, 310, [b])  # 和上一条（recent 里，1 核重叠 10 秒）、b（2 核整 20 秒）都重叠
        self.assertEqual(doc["calibration"]["samples"][-1]["load"], 3.5)
        self.assertEqual(doc["calibration"]["capacity"], 3.0)  # 连续 2 个明显变慢
        self.assertEqual(self.d.heavy_capacity(doc), (3.0, "按实测校准过，从测试硬件 4 起步"))
        doc = self.add("tiny", 2, 320, [b], small=True)
        self.assertEqual(len(doc["calibration"]["samples"]), 2)
        self.assertEqual(doc["exact"]["r|tiny"]["solo"], [])
        doc = self.add("a", 20, 330, [b], exit_code=1)
        self.assertEqual(len(doc["calibration"]["samples"]), 2)

    def test_baseline_only_same_command(self) -> None:
        """参数不同的同形状命令不互为基准：a 单独跑过 2 次，同形状的 b 同时跑完不出样本；a 自己同时跑完才出样本"""
        self.d.HEAVY_DIR.mkdir(parents=True)
        self.add("s", 10, 100, cmd="s -p a.py")
        self.add("s", 10, 200, cmd="s -p a.py")
        b = {"id": "x", "state": "running", "started_at": 280.0, "reserve": 2.0}
        doc = self.add("s", 30, 300, [b], cmd="s -p b.py")
        self.assertNotIn("samples", doc["calibration"])
        doc = self.add("s", 20, 400, [dict(b, started_at=380.0)], cmd="s -p a.py")
        (sample,) = doc["calibration"]["samples"]
        self.assertEqual((sample["command"], sample["slowdown"]), ("s -p a.py", 2.0))

    def test_baseline_needs_two_solo_runs(self) -> None:
        """同一条命令只单独跑过 1 次：同时跑完不出样本；单独跑满 2 次之后才出"""
        self.d.HEAVY_DIR.mkdir(parents=True)
        b = {"id": "x", "state": "running", "started_at": 0.0, "reserve": 2.0}
        self.add("a", 10, 100)
        doc = self.add("a", 20, 200, [b])
        self.assertNotIn("samples", doc["calibration"])
        self.add("a", 10, 300)
        doc = self.add("a", 20, 400, [b])
        self.assertEqual(len(doc["calibration"]["samples"]), 1)

    def test_jittery_baseline_no_sample(self) -> None:
        """单独跑本身就抖（最慢 ÷ 最快 > heavy_calib_fast 1.2）：不出样本，再单独跑一次快的也还抖；差 1.2 倍以内的出"""
        self.d.HEAVY_DIR.mkdir(parents=True)
        b = {"id": "x", "state": "running", "started_at": 0.0, "reserve": 2.0}
        self.add("a", 10, 100)
        self.add("a", 15, 200)
        self.assertNotIn("samples", self.add("a", 30, 300, [b])["calibration"])
        self.add("a", 10, 400)
        self.assertNotIn("samples", self.add("a", 30, 500, [b])["calibration"])  # [10, 15, 10] 还是 1.5 倍
        self.add("c", 10, 600)
        self.add("c", 11, 700)
        (sample,) = self.add("c", 21, 800, [b])["calibration"]["samples"]
        self.assertEqual((sample["base"], sample["slowdown"]), (10.5, 2.0))

    def test_old_state_discarded(self) -> None:
        """旧版（校准规则版本 1）的状态：同形状不同文件混在一起的基准、被它们降下来的容量和 fails 作废，容量回到硬件值；
        之后再来不同文件的同时跑也不出样本、不降容量。--status 也显示硬件值"""
        hw = self.real_hardware
        if hw < 2:
            self.skipTest("本机硬件值不到 2 核，造不出降过一核的旧状态")
        self.d.heavy_hardware = lambda: (hw, "测试硬件")
        self.d.heavy_logical_cores = lambda: int(hw)
        shape = "python3 -m unittest discover -s selftest -p <1 个文件>"
        old_sample = {"at": 1795046333, "shape": shape, "repo": "drover", "load": 2.9, "slowdown": 1.92, "verdict": "slow",
                      "capacity": hw}
        old = {"keys": {"s": {"family": "f", "shape": shape, "repo": "drover", "last": 1795046333,
                              "runs": [{"at": 1795046229, "wall": 35.8, "cpu": 26.88, "exit": 0}],
                              "solo": [{"wall": 3.1, "ok": True}, {"wall": 7.4, "ok": True}, {"wall": 35.8, "ok": True}]}},
               "recent": [],
               "calibration": {"hardware": hw, "capacity": hw - 1, "streak": [], "samples": [old_sample] * 2,
                               "changes": [{"at": 1795046333, "from": hw, "to": hw - 1, "reason": "连续 2 个同时跑的明显变慢"}],
                               "fails": {f"{hw:g}": 1}}}
        self.heavy.mkdir(parents=True)
        (self.heavy / "history.json").write_text(json.dumps(old))
        self.assertEqual(self.d.heavy_capacity(), (hw, "测试硬件"))
        status = self.drover("heavy", "--status").stdout
        self.assertIn(f"容量 {hw:g} 核（", status)
        self.assertNotIn("按实测校准过", status)
        self.assertIn("还没有样本", status)
        b = {"id": "x", "state": "running", "started_at": 1795046300.0, "reserve": 2.0}
        doc = self.add("s", 14.2, 1795046333, [b], cmd=shape.replace("<1 个文件>", "test_board.py"))
        self.assertEqual((doc["calibration"], self.d.heavy_capacity(doc)), ({}, (hw, "测试硬件")))
        self.assertNotIn("solo", doc["keys"]["s"])  # 旧的形状级基准随条目重写消失
        current = dict(old["calibration"], version=self.d.HEAVY_CAL_VERSION)  # 对照：本版本的状态照用
        (self.heavy / "history.json").write_text(json.dumps(dict(old, calibration=current)))
        self.assertEqual(self.d.heavy_capacity()[0], hw - 1)

    def test_fixed_capacity_not_calibrated(self) -> None:
        """config 写了 heavy_cpu_capacity：照样记实测，但不校准、容量就是它"""
        self.set_config(heavy_cpu_capacity=6)
        self.d = self.load_drover()
        self.d.HEAVY_DIR.mkdir(parents=True)
        self.add("a", 10, 100)
        self.add("a", 10, 150)
        doc = self.add("a", 30, 200, [{"id": "b", "state": "running", "started_at": 170.0, "reserve": 3.0}])
        self.assertEqual(doc["calibration"], {})
        self.assertEqual(self.d.heavy_capacity(doc), (6.0, "config 的 heavy_cpu_capacity，固定、不校准"))

    def test_killed_and_timed_out_not_recorded(self) -> None:
        """被信号杀掉、超时的不记实测（也就不会成为样本或基准）"""
        self.drover("heavy", "--", sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)", rc=None)
        res = self.d.heavy_run([sys.executable, "-c", "import time; time.sleep(5)"], str(self.tmp), timeout=0.3)
        self.assertTrue(res["timed_out"])
        self.assertEqual(self.d.heavy_doc()["keys"], {})

    def test_status_shows_calibration(self) -> None:
        """--status：硬件值、现在用的值、最近一次调整和依据、最近的样本（完整命令、负载、变慢倍数、基准次数和中位数）"""
        hw = self.real_hardware
        if hw < 2:
            self.skipTest("本机硬件值不到 2 核，造不出调小一核的校准状态")
        sample = {"at": time.time(), "repo": "app", "command": "bunx vitest run src/a.test.ts", "slowdown": 2.1,
                  "load": 3.5, "base": 10.0, "base_runs": 3, "verdict": "slow", "capacity": hw}
        cal = {"version": self.d.HEAVY_CAL_VERSION, "hardware": hw, "capacity": hw - 1, "streak": [], "samples": [sample],
               "changes": [{"at": time.time(), "from": hw, "to": hw - 1, "reason": "连续 2 个同时跑的明显变慢"}]}
        self.heavy.mkdir(parents=True)
        (self.heavy / "history.json").write_text(json.dumps({"keys": {}, "recent": [], "calibration": cal}))
        status = self.drover("heavy", "--status").stdout
        self.assertIn(f"容量 {hw - 1:g} 核（按实测校准过", status)
        self.assertIn(f"容量校准：硬件值 {hw:g} 核", status)
        self.assertIn(f"现在用 {hw - 1:g} 核", status)
        self.assertIn(f"从 {hw:g} 到 {hw - 1:g} 核，连续 2 个同时跑的明显变慢", status)
        self.assertIn("app: bunx vitest run src/a.test.ts  负载 3.5 核，变慢 2.1 倍（单独跑 3 次中位 10.0 秒） → 明显变慢",
                      status)


class WorkerSetupTests(SchedTestCase):
    def test_codex_worker_can_write_sched_dir(self) -> None:
        """agent-start：codex worker 加 --add-dir <状态目录>/locks/heavy（沙箱里也能进调度）；claude worker 不加；
        dry-run 不建目录"""
        codex = self.drover_json("agent-start", "--name", "w1", "--kind", "codex", "--slug", "s1", "--pane", "w1:p1",
                                 "--dry-run")
        self.assertIn(f"--add-dir {self.heavy}", codex["planned"][0])
        claude = self.drover_json("agent-start", "--name", "w2", "--kind", "claude", "--slug", "s1", "--pane", "w1:p1",
                                  "--dry-run")
        self.assertNotIn("locks", claude["planned"][0])
        self.assertFalse(self.heavy.exists())

    def test_brief_heavy_rule(self) -> None:
        """brief 的约束：测试、lint、类型检查、构建一律经字面的 ~/.drover/bin/drover heavy；轻的不排队；
        没有"同一时间只跑一个"的旧说法；只跑相关测试照旧"""
        repo = self.work / "app"
        self.init_repo(repo, {"README.md": "x\n"})
        deps = self.tmp / "deps.json"
        deps.write_text(json.dumps({"worktree_path": str(repo), "summary_for_prompt": "- 无。", "items": []}))
        task = self.tmp / "task.md"
        task.write_text("### 背景\n测试用。\n\n### 要做的\n什么都不做。\n\n### 不做\n不改任何文件。\n")
        for kind in ("claude", "codex"):
            with self.subTest(kind=kind):
                prompt = self.drover_json("brief", "--slug", "s1", "--name", "w1", "--kind", kind, "--repo", str(repo),
                                          "--wt", str(repo), "--deps-json", str(deps), "--task-file", str(task),
                                          "--goal", "目标", "--evidence", "证据", "--dry-run")["prompt"]
                self.assertIn("测试、lint、类型检查、构建一律经 `~/.drover/bin/drover heavy -- <命令>` 执行", prompt)
                self.assertIn("轻的、跑得快的不用排队", prompt)
                self.assertIn("不要跑全量测试", prompt)
                self.assertNotIn("同一时间只跑一个", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
