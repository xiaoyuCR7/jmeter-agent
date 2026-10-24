import os
import csv
import re
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from array import array
from collections import Counter, defaultdict

from app.models.schemas import TestSummary, DetailedTestSummary, ErrorDetail, TimelineSegment, PerformanceTrend

logger = logging.getLogger(__name__)


class ReportParser:
    """JMeter报告解析器"""

    @staticmethod
    def _safe_int(val: Any, default: int = 0) -> int:
        """Convert field to int, returning default on empty/None/invalid."""
        if val is None or val == "":
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_str(val: Any, default: str = "") -> str:
        """Convert field to str, returning default on None."""
        if val is None:
            return default
        return str(val)

    @staticmethod
    def _bucket_sort_key(key: str) -> int:
        """Safe sort key for time-bucket strings like '0-30s' or '-30-0s'."""
        try:
            return int(key.split("-")[0])
        except (ValueError, IndexError):
            return 0

    def parse_jtl(self, jtl_path: str) -> DetailedTestSummary:
        """解析JTL结果文件，返回详细的测试摘要"""
        if not os.path.exists(jtl_path):
            raise FileNotFoundError(f"JTL file not found: {jtl_path}")

        # A file JMeter is still flushing must settle first; finished reports need no wait.
        if time.time() - os.path.getmtime(jtl_path) < self.SETTLED_AFTER_SECONDS:
            self._wait_file_stable(jtl_path)

        with open(jtl_path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            f.seek(0)

            if first_line.startswith("<"):
                return self._parse_xml_jtl(f)
            else:
                return self._parse_csv_jtl(f)

    SETTLED_AFTER_SECONDS = 2.0

    def _wait_file_stable(self, path: str, max_wait: float = 5.0, interval: float = 0.5):
        """Wait until file size stops changing (JMeter finished flushing)."""
        deadline = time.time() + max_wait
        last_size = -1
        while time.time() < deadline:
            try:
                cur_size = os.path.getsize(path)
            except OSError:
                time.sleep(interval)
                continue
            if cur_size == last_size and cur_size > 0:
                return
            last_size = cur_size
            time.sleep(interval)
        logger.debug("File stabilization timeout for %s (size=%s)", path, last_size)

    def _parse_csv_jtl(self, file) -> DetailedTestSummary:
        """解析CSV格式的JTL，生成详细的分析报告数据"""
        # restval="" prevents None values in DictReader when rows are incomplete
        # (JMeter may still be flushing the CSV buffer when we start reading)
        acc = _Accumulator()
        for row in csv.DictReader(file, restval=""):
            acc.add(
                elapsed=self._safe_int(row.get("elapsed")),
                label=self._safe_str(row.get("label"), "Unnamed") or "Unnamed",
                success=self._safe_str(row.get("success"), "true").lower() == "true",
                code=self._safe_str(row.get("responseCode")),
                message=self._safe_str(row.get("responseMessage")),
                received=self._safe_int(row.get("bytes")),
                sent=self._safe_int(row.get("sentBytes")),
                ts=self._safe_int(row.get("timeStamp")),
            )
        return acc.summary(self) if acc.total else self._empty_summary()

    def _parse_xml_jtl(self, file) -> DetailedTestSummary:
        """解析XML格式的JTL；流式读取，处理完的元素立即释放。"""
        import xml.etree.ElementTree as ET

        acc = _Accumulator()
        for _, sample in ET.iterparse(file, events=("end",)):
            if sample.tag not in {"sample", "httpSample", "sampleResult"}:
                continue
            acc.add(
                elapsed=self._safe_int(sample.get("t")),
                label=sample.get("lb", "") or "Unnamed",
                success=sample.get("s", "true") == "true",
                code=sample.get("rc", ""),
                message=sample.get("rm", ""),
                received=self._safe_int(sample.get("by")),
                sent=self._safe_int(sample.get("sb")),
                ts=self._safe_int(sample.get("ts")),
            )
            sample.clear()
        return acc.summary(self) if acc.total else self._empty_summary()

    def _analyze_trend(self, segments: list) -> Optional[PerformanceTrend]:
        """分析性能趋势"""
        if len(segments) < 3:
            return None
        
        # 比较前半段和后半段的平均响应时间
        mid = len(segments) // 2
        first_half_avg = sum(s.avg_response_time for s in segments[:mid]) / mid if mid > 0 else 0
        second_half_avg = sum(s.avg_response_time for s in segments[mid:]) / (len(segments) - mid) if (len(segments) - mid) > 0 else 0
        
        first_half_err = sum(s.error_rate for s in segments[:mid]) / mid if mid > 0 else 0
        second_half_err = sum(s.error_rate for s in segments[mid:]) / (len(segments) - mid) if (len(segments) - mid) > 0 else 0
        
        # 检查波动
        rt_values = [s.avg_response_time for s in segments]
        rt_variance = max(rt_values) - min(rt_values)
        rt_mean = sum(rt_values) / len(rt_values)
        fluctuation_ratio = rt_variance / rt_mean if rt_mean > 0 else 0
        
        if fluctuation_ratio > 0.5:
            trend = "fluctuating"
            desc = f"性能波动较大，响应时间在 {min(rt_values):.0f}ms ~ {max(rt_values):.0f}ms 之间大幅变化，系统稳定性不足"
        elif second_half_avg > first_half_avg * 1.3:
            trend = "degrading"
            desc = f"性能呈恶化趋势，后半段平均响应时间({second_half_avg:.0f}ms)比前半段({first_half_avg:.0f}ms)增加了 {((second_half_avg/first_half_avg-1)*100):.0f}%"
            if second_half_err > first_half_err * 1.5 and second_half_err > 1:
                desc += f"，且后半段错误率({second_half_err:.1f}%)显著上升"
        elif second_half_avg < first_half_avg * 0.8:
            trend = "improving"
            desc = f"性能呈改善趋势，后半段平均响应时间({second_half_avg:.0f}ms)比前半段({first_half_avg:.0f}ms)降低了 {((1-second_half_avg/first_half_avg)*100):.0f}%"
        else:
            trend = "stable"
            desc = f"性能总体稳定，前后半段平均响应时间分别为 {first_half_avg:.0f}ms 和 {second_half_avg:.0f}ms"
            if second_half_err > 5:
                desc += f"，但存在 {second_half_err:.1f}% 的持续错误率"
        
        return PerformanceTrend(trend_direction=trend, description=desc)

    @staticmethod
    def _histogram_percentiles(histogram: Counter, count: int, points=(0.5, 0.9, 0.95, 0.99)) -> list:
        """Exact linear-interpolated percentiles from an integer-millisecond histogram."""
        if not count:
            return [0 for _ in points]
        wanted = {}
        for p in points:
            k = (count - 1) * p
            wanted.setdefault(int(k), None)
            wanted.setdefault(min(int(k) + 1, count - 1), None)
        targets = sorted(wanted)
        seen, index = 0, 0
        for value in sorted(histogram):
            seen += histogram[value]
            while index < len(targets) and targets[index] < seen:
                wanted[targets[index]] = value
                index += 1
            if index == len(targets):
                break
        result = []
        for p in points:
            k = (count - 1) * p
            low, high = int(k), min(int(k) + 1, count - 1)
            result.append(wanted[low] if low == high else wanted[low] * (high - k) + wanted[high] * (k - low))
        return result

    def _percentile(self, sorted_data: list, p: float) -> float:
        """计算百分位数"""
        if not sorted_data:
            return 0
        k = (len(sorted_data) - 1) * p
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_data) else f
        if f == c:
            return sorted_data[f]
        return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)

    def _empty_summary(self) -> DetailedTestSummary:
        return DetailedTestSummary(
            total_requests=0,
            avg_response_time=0,
            min_response_time=0,
            max_response_time=0,
            p95_response_time=0,
            p99_response_time=0,
            error_rate=0,
            throughput=0,
            received_bytes_per_sec=0,
            sent_bytes_per_sec=0,
            percentiles={"p50": 0, "p90": 0, "p95": 0, "p99": 0},
            error_details=[],
            timeline_segments=[],
            performance_trend=None,
            response_time_distribution={}
        )

    def parse_dashboard_json(self, report_dir: str) -> dict:
        """Parse JMeter HTML dashboard statistics.json for chart data interpretation.

        JMeter generates an HTML dashboard with statistics.json containing
        pre-computed aggregates for all charts (response time, throughput, etc.).
        This lets the agent interpret chart data without needing image analysis.

        Returns structured chart data: per-transaction stats, percentiles,
        throughput trends, error breakdown — everything the dashboard charts show.
        """
        html_dir = os.path.join(report_dir, "html")
        stats_path = os.path.join(html_dir, "statistics.json")

        if not os.path.exists(stats_path):
            # Try alternate locations
            alt_paths = [
                os.path.join(report_dir, "statistics.json"),
                os.path.join(report_dir, "html", "content", "js", "dashboard.js"),
            ]
            for alt in alt_paths:
                if os.path.exists(alt):
                    stats_path = alt
                    break
            else:
                return {"error": "No statistics.json found in report directory",
                        "report_dir": report_dir}

        try:
            import json
            with open(stats_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Handle dashboard.js format (JSON embedded in JS)
            if stats_path.endswith(".js"):
                # Extract JSON from JavaScript
                import re
                match = re.search(r'createTable\((\[.*\]|{.*})\)', content, re.DOTALL)
                if match:
                    content = match.group(1)

            data = json.loads(content)

            # JMeter statistics.json structure: array of transaction objects
            # or { "items": [...] } or raw array
            items = data if isinstance(data, list) else data.get("items", data.get("transactions", []))

            if not items and isinstance(data, dict):
                # Flat structure: {"Transaction Name": {...}}
                items = [
                    {"transaction": k, **v}
                    for k, v in data.items()
                    if isinstance(v, dict) and "sampleCount" in v
                ]

            transactions = []
            total_stats = None

            for item in items:
                if not isinstance(item, dict):
                    continue
                tx = {
                    "name": item.get("transaction", item.get("label", "Unknown")),
                    "sample_count": item.get("sampleCount", item.get("samples", 0)),
                    "error_count": item.get("errorCount", item.get("errors", 0)),
                    "error_pct": round(item.get("errorPct", item.get("errorRate", 0)), 2),
                    "avg_response_time": round(item.get("meanResTime", item.get("avg", 0)), 2),
                    "median_response_time": round(item.get("medianResTime", item.get("median", 0)), 2),
                    "min_response_time": item.get("minResTime", item.get("min", 0)),
                    "max_response_time": item.get("maxResTime", item.get("max", 0)),
                    "p90_response_time": round(item.get("pct1ResTime", item.get("line90Rt", 0)), 2),
                    "p95_response_time": round(item.get("pct2ResTime", item.get("line95Rt", 0)), 2),
                    "p99_response_time": round(item.get("pct3ResTime", item.get("line99Rt", 0)), 2),
                    "throughput": round(item.get("throughput", item.get("tps", 0)), 2),
                    "received_kb_per_sec": round(item.get("receivedKBytesPerSec", item.get("recvBytesPerSec", 0) / 1024 if item.get("recvBytesPerSec") else 0), 2),
                    "sent_kb_per_sec": round(item.get("sentKBytesPerSec", item.get("sentBytesPerSec", 0) / 1024 if item.get("sentBytesPerSec") else 0), 2),
                }
                if tx["name"] == "Total":
                    total_stats = tx
                transactions.append(tx)

            # Sort: Total first, then by throughput descending
            transactions.sort(key=lambda t: (0 if t["name"] == "Total" else 1, -t["throughput"]))

            # Chart-level insights
            insights = self._generate_chart_insights(transactions, total_stats)

            return {
                "total_statistics": total_stats,
                "transactions": transactions,
                "transaction_count": len(transactions),
                "insights": insights,
                "source": os.path.basename(stats_path),
            }

        except Exception as e:
            return {"error": f"Failed to parse statistics.json: {str(e)}",
                    "report_dir": report_dir}

    def _generate_chart_insights(self, transactions: list, total: dict) -> list[str]:
        """Generate human-readable insights from dashboard chart data."""
        insights = []
        if not total:
            return insights

        # Overall health
        error_pct = total.get("error_pct", 0)
        if error_pct == 0:
            insights.append("✅ Zero errors — test ran cleanly")
        elif error_pct < 1:
            insights.append(f"⚠️ Low error rate: {error_pct}%")
        else:
            insights.append(f"🚨 High error rate: {error_pct}% — investigate errors")

        # Throughput vs latency
        p99 = total.get("p99_response_time", 0)
        tps = total.get("throughput", 0)
        avg = total.get("avg_response_time", 0)

        if avg > 0 and p99 > 0:
            if p99 > avg * 5:
                insights.append(
                    f"📊 High tail latency: P99 ({p99}ms) is {p99/avg:.0f}x the average ({avg}ms) "
                    f"— possible GC pauses, lock contention, or external dependency slowdown"
                )
            elif p99 > avg * 3:
                insights.append(
                    f"📊 Moderate tail latency: P99 ({p99}ms) vs avg ({avg}ms)"
                )

        if tps > 0:
            insights.append(f"📈 Throughput: {tps} req/s across {total.get('sample_count', 0)} samples")

        # Identify slowest transactions
        non_total = [t for t in transactions if t["name"] != "Total" and t.get("avg_response_time", 0) > 0]
        if non_total:
            slowest = sorted(non_total, key=lambda t: t["avg_response_time"], reverse=True)[:3]
            insights.append(
                "🐌 Slowest endpoints: " +
                ", ".join(f"{t['name']} (avg={t['avg_response_time']}ms, P99={t['p99_response_time']}ms)"
                          for t in slowest)
            )

            # Error hotspots
            error_txs = [t for t in non_total if t.get("error_pct", 0) > 0]
            if error_txs:
                insights.append(
                    "🔴 Error hotspots: " +
                    ", ".join(f"{t['name']} ({t['error_pct']}%)" for t in error_txs[:5])
                )

        return insights

    def get_html_report_url(self, report_dir: Optional[str]) -> Optional[str]:
        """获取HTML报告路径"""
        if not report_dir:
            return None
        html_dir = os.path.join(report_dir, "html")
        index_path = os.path.join(html_dir, "index.html")
        if os.path.exists(index_path):
            return index_path
        return None

    def read_log(self, log_path: str, tail_lines: int = 100) -> str:
        """读取日志文件尾部"""
        if not os.path.exists(log_path):
            return ""
        
        if not 1 <= tail_lines <= 5000:
            raise ValueError('tail_lines must be within [1, 5000]')
        with open(log_path, 'rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            text = stream.read(65536).decode('utf-8', errors='replace')
        return ''.join(text.splitlines(keepends=True)[-tail_lines:])


_RT_BANDS = (("0-100ms", 100), ("100-300ms", 300), ("300-500ms", 500), ("500ms-1s", 1000),
             ("1s-3s", 3000), ("3s-5s", 5000), (">5s", None))


class _Accumulator:
    """Streaming JTL aggregation shared by the CSV and XML readers.

    Response times are integer milliseconds, so percentiles come exactly from
    histograms whose size is the number of distinct values, not samples. Only
    timeline inputs are kept per sample, in compact typed arrays.
    """

    def __init__(self):
        self.total = self.errors = self.received = self.sent = 0
        self.elapsed_sum = 0
        self.histogram = Counter()
        self.bands = dict.fromkeys((name for name, _ in _RT_BANDS), 0)
        self.labels = {}
        self.error_details = defaultdict(lambda: {"count": 0, "messages": []})
        self.ts, self.elapsed, self.ok = array("q"), array("q"), array("b")

    def add(self, *, elapsed, label, success, code, message, received, sent, ts):
        self.total += 1
        self.elapsed_sum += elapsed
        self.histogram[elapsed] += 1
        for name, limit in _RT_BANDS:
            if limit is None or elapsed < limit:
                self.bands[name] += 1
                break
        stats = self.labels.get(label)
        if stats is None:
            stats = self.labels[label] = {"count": 0, "errors": 0, "sum": 0, "received": 0, "histogram": Counter()}
        stats["count"] += 1
        stats["sum"] += elapsed
        stats["received"] += received
        stats["histogram"][elapsed] += 1
        if not success:
            self.errors += 1
            stats["errors"] += 1
            key = f"{code}: {message}" if code else message
            if not key or key == ": ":
                key = "Unknown Error"
            detail = self.error_details[key]
            detail["count"] += 1
            if len(detail["messages"]) < 3:
                detail["messages"].append(message or "No message")
        self.received += received
        self.sent += sent
        if ts > 0:
            self.ts.append(ts)
            self.elapsed.append(elapsed)
            self.ok.append(1 if success else 0)

    def _timeline(self):
        """Use the earliest start and latest end, independent of row order."""
        if not self.ts:
            return 1.0, []
        first = min(self.ts)
        last_end = max(t + max(e, 0) for t, e in zip(self.ts, self.elapsed))
        duration = max((last_end - first) / 1000.0, 0.001)
        buckets = {}
        for t, e, ok in zip(self.ts, self.elapsed, self.ok):
            index = (t - first) // 30000
            bucket = buckets.get(index)
            if bucket is None:
                bucket = buckets[index] = [0, 0, 0, 0]  # count, sum, max, errors
            bucket[0] += 1
            bucket[1] += e
            bucket[2] = max(bucket[2], e)
            bucket[3] += 0 if ok else 1
        segments = []
        for index in sorted(buckets):
            count, total, peak, errors = buckets[index]
            start = index * 30
            window = max(min(30.0, duration - start), 0.001)  # Tail buckets use their observed duration
            segments.append(TimelineSegment(
                time_range=f"{start}-{start + 30}s", avg_response_time=round(total / count, 2),
                max_response_time=peak, throughput=round(count / window, 2),
                error_rate=round(errors / count * 100, 2), request_count=count))
        return duration, segments

    def summary(self, parser) -> DetailedTestSummary:
        duration, segments = self._timeline()
        per_label = {}
        for label, stats in self.labels.items():
            p90, p95, p99 = parser._histogram_percentiles(stats["histogram"], stats["count"], (0.9, 0.95, 0.99))
            per_label[label] = {
                "count": stats["count"],
                "avg_response_time": round(stats["sum"] / stats["count"], 2),
                "min_response_time": min(stats["histogram"]),
                "max_response_time": max(stats["histogram"]),
                "p90_response_time": round(p90, 2),
                "p95_response_time": round(p95, 2),
                "p99_response_time": round(p99, 2),
                "error_rate": round(stats["errors"] / stats["count"] * 100, 2),
                "throughput": round(stats["count"] / duration, 2),
                "bytes_per_sec": round(stats["received"] / duration, 2),
            }
        p50, p90, p95, p99 = (round(v, 2) for v in parser._histogram_percentiles(self.histogram, self.total))
        errors = [ErrorDetail(error_type=key[:100], count=data["count"],
                              percentage=round(data["count"] / self.total * 100, 2),
                              sample_messages=data["messages"][:3])
                  for key, data in sorted(self.error_details.items(), key=lambda x: x[1]["count"], reverse=True)[:10]]
        return DetailedTestSummary(
            total_requests=self.total,
            avg_response_time=round(self.elapsed_sum / self.total, 2),
            min_response_time=min(self.histogram),
            max_response_time=max(self.histogram),
            p95_response_time=p95,
            p99_response_time=p99,
            error_rate=round(self.errors / self.total * 100, 2),
            throughput=round(self.total / duration, 2),
            received_bytes_per_sec=round(self.received / duration, 2),
            sent_bytes_per_sec=round(self.sent / duration, 2),
            percentiles={"p50": p50, "p90": p90, "p95": p95, "p99": p99},
            error_details=errors,
            timeline_segments=segments,
            performance_trend=parser._analyze_trend(segments),
            response_time_distribution={k: round(v / self.total * 100, 2) for k, v in self.bands.items() if v > 0},
            per_label=per_label or None,
        )
