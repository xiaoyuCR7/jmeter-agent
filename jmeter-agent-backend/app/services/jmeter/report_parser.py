import os
import csv
import re
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from collections import defaultdict

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

    @staticmethod
    def _timing_metrics(samples):
        """Use the earliest sample start and latest response end, independent of row order."""
        buckets = defaultdict(lambda: {"response_times": [], "errors": 0, "count": 0, "bytes": 0})
        if not samples:
            return 1.0, buckets
        first_ts = min(sample[0] for sample in samples)
        last_end = max(ts + max(elapsed, 0) for ts, elapsed, _, _ in samples)
        duration = max((last_end - first_ts) / 1000.0, 0.001)
        for ts, elapsed, received, success in samples:
            start = ((ts - first_ts) // 30000) * 30
            bucket = buckets[f"{start}-{start + 30}s"]
            bucket["response_times"].append(elapsed)
            bucket["count"] += 1
            bucket["bytes"] += received
            bucket["errors"] += int(not success)
            bucket["duration"] = max(min(30.0, duration - start), 0.001)
        return duration, buckets

    def parse_jtl(self, jtl_path: str) -> DetailedTestSummary:
        """解析JTL结果文件，返回详细的测试摘要"""
        if not os.path.exists(jtl_path):
            raise FileNotFoundError(f"JTL file not found: {jtl_path}")

        # Brief stabilization wait — JMeter may still be flushing CSV buffers
        self._wait_file_stable(jtl_path)

        with open(jtl_path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            f.seek(0)

            if first_line.startswith("<"):
                return self._parse_xml_jtl(f)
            else:
                return self._parse_csv_jtl(f)

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
        reader = csv.DictReader(file, restval="")

        response_times = []
        errors = 0
        total = 0
        received_bytes_total = 0
        sent_bytes_total = 0
        timed_samples = []
        
        # 错误详情收集
        error_details = defaultdict(lambda: {"count": 0, "messages": []})
        
        # 响应时间分布
        rt_distribution = {"0-100ms": 0, "100-300ms": 0, "300-500ms": 0,
                          "500ms-1s": 0, "1s-3s": 0, "3s-5s": 0, ">5s": 0}

        # 按采样器标签(接口)分组统计
        per_label_data = defaultdict(lambda: {
            "response_times": [], "errors": 0, "count": 0,
            "bytes_received": 0, "bytes_sent": 0,
        })

        for row in reader:
            total += 1
            try:
                elapsed = self._safe_int(row.get("elapsed"))
                response_times.append(elapsed)
                label = self._safe_str(row.get("label"), "Unnamed")

                # Per-label tracking
                per_label_data[label]["response_times"].append(elapsed)
                per_label_data[label]["count"] += 1

                # 响应时间分布
                if elapsed < 100:
                    rt_distribution["0-100ms"] += 1
                elif elapsed < 300:
                    rt_distribution["100-300ms"] += 1
                elif elapsed < 500:
                    rt_distribution["300-500ms"] += 1
                elif elapsed < 1000:
                    rt_distribution["500ms-1s"] += 1
                elif elapsed < 3000:
                    rt_distribution["1s-3s"] += 1
                elif elapsed < 5000:
                    rt_distribution["3s-5s"] += 1
                else:
                    rt_distribution[">5s"] += 1

                # 错误统计 — _safe_str prevents None.lower() crash on incomplete rows
                success = self._safe_str(row.get("success"), "true").lower() == "true"
                response_code = self._safe_str(row.get("responseCode"))
                response_message = self._safe_str(row.get("responseMessage"))

                if not success:
                    errors += 1
                    per_label_data[label]["errors"] += 1
                    error_key = f"{response_code}: {response_message}" if response_code else response_message
                    if not error_key or error_key == ": ":
                        error_key = "Unknown Error"
                    error_details[error_key]["count"] += 1
                    if len(error_details[error_key]["messages"]) < 3:
                        error_details[error_key]["messages"].append(response_message or "No message")

                bytes_received = self._safe_int(row.get("bytes"))
                received_bytes_total += bytes_received
                per_label_data[label]["bytes_received"] += bytes_received

                sent_bytes = self._safe_int(row.get("sentBytes"))
                sent_bytes_total += sent_bytes
                per_label_data[label]["bytes_sent"] += sent_bytes

                ts = self._safe_int(row.get("timeStamp"))
                if ts > 0:
                    timed_samples.append((ts, elapsed, bytes_received, success))

            except (ValueError, KeyError):
                continue

        if total == 0:
            return self._empty_summary()

        response_times.sort()

        # 计算持续时间（必须在 per-label 统计之前）
        duration_seconds, time_bucket_data = self._timing_metrics(timed_samples)

        # ── Per-label statistics ──────────────────────────
        per_label_stats = {}
        for label, data in per_label_data.items():
            if data["count"] == 0:
                continue
            rts = sorted(data["response_times"])
            per_label_stats[label] = {
                "count": data["count"],
                "avg_response_time": round(sum(rts) / len(rts), 2),
                "min_response_time": rts[0],
                "max_response_time": rts[-1],
                "p95_response_time": round(self._percentile(rts, 0.95), 2),
                "p99_response_time": round(self._percentile(rts, 0.99), 2),
                "error_rate": round(data["errors"] / data["count"] * 100, 2),
                "throughput": round(data["count"] / duration_seconds, 2),
                "bytes_per_sec": round(data["bytes_received"] / duration_seconds, 2),
            }

        # 构建时间段分析
        timeline_segments = []
        for bucket_key in sorted(
            (k for k in time_bucket_data.keys() if k and "-" in k),
            key=self._bucket_sort_key,
        ):
            bucket = time_bucket_data[bucket_key]
            rts = bucket["response_times"]
            if rts:
                timeline_segments.append(TimelineSegment(
                    time_range=bucket_key,
                    avg_response_time=round(sum(rts) / len(rts), 2),
                    max_response_time=max(rts),
                    throughput=round(bucket["count"] / bucket["duration"], 2),  # Tail buckets use their observed duration
                    error_rate=round(bucket["errors"] / bucket["count"] * 100, 2) if bucket["count"] > 0 else 0,
                    request_count=bucket["count"]
                ))

        # 构建错误详情
        error_list = []
        for error_key, data in sorted(error_details.items(), key=lambda x: x[1]["count"], reverse=True)[:10]:
            error_list.append(ErrorDetail(
                error_type=error_key[:100],  # 限制长度
                count=data["count"],
                percentage=round(data["count"] / total * 100, 2),
                sample_messages=data["messages"][:3]
            ))

        # 性能趋势分析
        trend = self._analyze_trend(timeline_segments)

        # 响应时间分布百分比
        rt_dist_pct = {k: round(v / total * 100, 2) for k, v in rt_distribution.items() if v > 0}

        # Compute percentiles for both nested dict and top-level fields (SLA compat)
        p50_val = round(self._percentile(response_times, 0.5), 2)
        p90_val = round(self._percentile(response_times, 0.9), 2)
        p95_val = round(self._percentile(response_times, 0.95), 2)
        p99_val = round(self._percentile(response_times, 0.99), 2)

        return DetailedTestSummary(
            total_requests=total,
            avg_response_time=round(sum(response_times) / len(response_times), 2) if response_times else 0,
            min_response_time=response_times[0] if response_times else 0,
            max_response_time=response_times[-1] if response_times else 0,
            p95_response_time=p95_val,
            p99_response_time=p99_val,
            error_rate=round(errors / total * 100, 2),
            throughput=round(total / duration_seconds, 2),
            received_bytes_per_sec=round(received_bytes_total / duration_seconds, 2),
            sent_bytes_per_sec=round(sent_bytes_total / duration_seconds, 2),
            percentiles={
                "p50": p50_val,
                "p90": p90_val,
                "p95": p95_val,
                "p99": p99_val,
            },
            error_details=error_list,
            timeline_segments=timeline_segments,
            performance_trend=trend,
            response_time_distribution=rt_dist_pct,
            per_label=per_label_stats if per_label_stats else None,
        )

    def _parse_xml_jtl(self, file) -> DetailedTestSummary:
        """解析XML格式的JTL（简化版，先转CSV逻辑处理）"""
        import xml.etree.ElementTree as ET
        
        tree = ET.parse(file)
        root = tree.getroot()
        
        response_times = []
        errors = 0
        total = 0
        received_bytes_total = 0
        sent_bytes_total = 0
        timed_samples = []

        error_details = defaultdict(lambda: {"count": 0, "messages": []})
        rt_distribution = {"0-100ms": 0, "100-300ms": 0, "300-500ms": 0,
                          "500ms-1s": 0, "1s-3s": 0, "3s-5s": 0, ">5s": 0}
        per_label_data = defaultdict(lambda: {
            "response_times": [], "errors": 0, "count": 0,
            "bytes_received": 0, "bytes_sent": 0,
        })


        for sample in root.iter():
            if sample.tag not in {"sample", "httpSample", "sampleResult"}:
                continue
            total += 1
            try:
                label = sample.get("lb", "") or "Unnamed"
                elapsed = int(sample.get("t", 0))
                response_times.append(elapsed)

                # Per-label tracking
                per_label_data[label]["response_times"].append(elapsed)
                per_label_data[label]["count"] += 1
                
                if elapsed < 100:
                    rt_distribution["0-100ms"] += 1
                elif elapsed < 300:
                    rt_distribution["100-300ms"] += 1
                elif elapsed < 500:
                    rt_distribution["300-500ms"] += 1
                elif elapsed < 1000:
                    rt_distribution["500ms-1s"] += 1
                elif elapsed < 3000:
                    rt_distribution["1s-3s"] += 1
                elif elapsed < 5000:
                    rt_distribution["3s-5s"] += 1
                else:
                    rt_distribution[">5s"] += 1
                
                success = sample.get("s", "true") == "true"
                response_code = sample.get("rc", "")
                response_message = sample.get("rm", "")
                
                if not success:
                    errors += 1
                    per_label_data[label]["errors"] += 1
                    error_key = f"{response_code}: {response_message}" if response_code else response_message
                    if not error_key or error_key == ": ":
                        error_key = "Unknown Error"
                    error_details[error_key]["count"] += 1
                    if len(error_details[error_key]["messages"]) < 3:
                        error_details[error_key]["messages"].append(response_message or "No message")

                bytes_received = int(sample.get("by", 0) or 0)
                received_bytes_total += bytes_received
                per_label_data[label]["bytes_received"] += bytes_received

                sent_bytes = int(sample.get("sb", 0) or 0)
                sent_bytes_total += sent_bytes
                per_label_data[label]["bytes_sent"] += sent_bytes
                
                ts = int(sample.get("ts", 0))
                if ts > 0:
                    timed_samples.append((ts, elapsed, bytes_received, success))

            except (ValueError, KeyError):
                continue
        
        if total == 0:
            return self._empty_summary()
        
        response_times.sort()
        duration_seconds, time_bucket_data = self._timing_metrics(timed_samples)
        
        timeline_segments = []
        for bucket_key in sorted(
            (k for k in time_bucket_data.keys() if k and "-" in k),
            key=self._bucket_sort_key,
        ):
            bucket = time_bucket_data[bucket_key]
            rts = bucket["response_times"]
            if rts:
                timeline_segments.append(TimelineSegment(
                    time_range=bucket_key,
                    avg_response_time=round(sum(rts) / len(rts), 2),
                    max_response_time=max(rts),
                    throughput=round(bucket["count"] / bucket["duration"], 2),
                    error_rate=round(bucket["errors"] / bucket["count"] * 100, 2) if bucket["count"] > 0 else 0,
                    request_count=bucket["count"]
                ))
        
        error_list = []
        for error_key, data in sorted(error_details.items(), key=lambda x: x[1]["count"], reverse=True)[:10]:
            error_list.append(ErrorDetail(
                error_type=error_key[:100],
                count=data["count"],
                percentage=round(data["count"] / total * 100, 2),
                sample_messages=data["messages"][:3]
            ))
        
        trend = self._analyze_trend(timeline_segments)
        rt_dist_pct = {k: round(v / total * 100, 2) for k, v in rt_distribution.items() if v > 0}

        # Per-label statistics
        per_label_stats = {}
        for lbl, data in per_label_data.items():
            if data["count"] == 0:
                continue
            rts = sorted(data["response_times"])
            per_label_stats[lbl] = {
                "count": data["count"],
                "avg_response_time": round(sum(rts) / len(rts), 2),
                "min_response_time": rts[0],
                "max_response_time": rts[-1],
                "p95_response_time": round(self._percentile(rts, 0.95), 2),
                "p99_response_time": round(self._percentile(rts, 0.99), 2),
                "error_rate": round(data["errors"] / data["count"] * 100, 2),
                "throughput": round(data["count"] / duration_seconds, 2),
                "bytes_per_sec": round(data["bytes_received"] / duration_seconds, 2),
            }

        p50_x = round(self._percentile(response_times, 0.5), 2)
        p90_x = round(self._percentile(response_times, 0.9), 2)
        p95_x = round(self._percentile(response_times, 0.95), 2)
        p99_x = round(self._percentile(response_times, 0.99), 2)

        return DetailedTestSummary(
            total_requests=total,
            avg_response_time=round(sum(response_times) / len(response_times), 2),
            min_response_time=response_times[0] if response_times else 0,
            max_response_time=response_times[-1] if response_times else 0,
            p95_response_time=p95_x,
            p99_response_time=p99_x,
            error_rate=round(errors / total * 100, 2),
            throughput=round(total / duration_seconds, 2),
            received_bytes_per_sec=round(received_bytes_total / duration_seconds, 2),
            sent_bytes_per_sec=round(sent_bytes_total / duration_seconds, 2),
            percentiles={
                "p50": p50_x, "p90": p90_x, "p95": p95_x, "p99": p99_x,
            },
            error_details=error_list,
            timeline_segments=timeline_segments,
            performance_trend=trend,
            response_time_distribution=rt_dist_pct,
            per_label=per_label_stats if per_label_stats else None,
        )

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
