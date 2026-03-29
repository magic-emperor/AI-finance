"""
Daily Report Generator

Generates end-of-day summary across all symbols:
- Per-symbol prediction accuracy
- Which data sources contributed to correct predictions
- AI provider usage breakdown
- Action items for tomorrow

Runs at market close (3:30 PM IST for NSE, 9:30 PM IST for US markets)
or on-demand via: python -m market_agent.runner.daily_report
"""

import os
from datetime import datetime, timedelta
import structlog

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from market_agent.data.storage.postgres import PostgresStorage
from market_agent.learning.signal_resolver import SignalResolver
from market_agent.runner.watchlist_scanner import WATCHLIST, EQUITY_SYMBOLS

logger = structlog.get_logger()


def generate_daily_report() -> dict:
    """
    Generate comprehensive daily report.
    Returns report dict with sections.
    """
    storage = PostgresStorage()
    resolver = SignalResolver(storage)
    
    print("=" * 60)
    print("  DAILY REPORT GENERATOR")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    report = {
        "date": datetime.now().isoformat(),
        "symbols": {},
        "global_stats": {},
        "ai_summary": None,
    }
    
    # 1. Per-symbol accuracy
    total_predictions = 0
    total_correct = 0
    best_symbol = {"name": "N/A", "accuracy": 0}
    worst_symbol = {"name": "N/A", "accuracy": 100}
    
    for symbol in WATCHLIST:
        try:
            stats = resolver.get_accuracy_stats(symbol=symbol)
            if stats and stats.get('total', 0) > 0:
                acc = stats.get('accuracy', 0)
                total = stats['total']
                total_predictions += total
                total_correct += int(total * acc / 100)
                
                report["symbols"][symbol] = {
                    "accuracy": round(acc, 1),
                    "total_predictions": total,
                    "trend": stats.get('trend', 'UNKNOWN'),
                }
                
                if acc > best_symbol["accuracy"]:
                    best_symbol = {"name": symbol, "accuracy": acc}
                if acc < worst_symbol["accuracy"]:
                    worst_symbol = {"name": symbol, "accuracy": acc}
                    
                print(f"  {symbol:20s} | {total:3d} predictions | {acc:5.1f}% accuracy | {stats.get('trend', 'N/A')}")
        except Exception:
            pass
    
    global_accuracy = (total_correct / total_predictions * 100) if total_predictions > 0 else 0
    report["global_stats"] = {
        "total_predictions": total_predictions,
        "global_accuracy": round(global_accuracy, 1),
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "symbols_tracked": len(report["symbols"]),
    }
    
    print(f"\n  GLOBAL: {total_predictions} predictions, {global_accuracy:.1f}% accuracy")
    print(f"  BEST:  {best_symbol['name']} ({best_symbol['accuracy']:.1f}%)")
    print(f"  WORST: {worst_symbol['name']} ({worst_symbol['accuracy']:.1f}%)")
    
    # 2. Brain training history
    try:
        training_runs = storage.get_brain_training_runs(limit=10)
        report["training_today"] = len(training_runs)
        print(f"\n  Training runs (last 10): {len(training_runs)}")
        for run in training_runs[:5]:
            print(f"    - {run.get('model_name', '?')}: {run.get('trigger_reason', '?')}")
    except Exception:
        report["training_today"] = 0
    
    # 3. AI-generated narrative (1 prompt)
    try:
        from market_agent.brain.gemini_client import gemini_client
        ai_summary = gemini_client.generate_daily_report(WATCHLIST)
        if ai_summary:
            report["ai_summary"] = ai_summary
            print(f"\n  AI NARRATIVE:\n  {ai_summary[:500]}")
        else:
            print("\n  AI NARRATIVE: Unavailable (rate limited or no API key)")
    except Exception as e:
        print(f"\n  AI NARRATIVE: Error - {e}")
    
    # 4. Save report to DB
    try:
        storage.log_brain_training_run(
            model_name="daily_report",
            dataset_size=total_predictions,
            accuracy_before=global_accuracy,
            accuracy_after=global_accuracy,
            trigger_reason=f"Daily report | Best: {best_symbol['name']} ({best_symbol['accuracy']:.1f}%) | Worst: {worst_symbol['name']} ({worst_symbol['accuracy']:.1f}%)",
        )
        print("\n  Report saved to brain_training_runs")
    except Exception:
        pass

    # 5. Save report to file so you "receive" it (e.g. for cron / scheduler)
    report_file = None
    try:
        import json
        from pathlib import Path
        reports_dir = Path(__file__).resolve().parents[2] / "reports"
        reports_dir.mkdir(exist_ok=True)
        report_file = reports_dir / f"daily_{datetime.now().strftime('%Y-%m-%d')}.json"
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\n  Report written to: {report_file}")
    except Exception as e:
        print(f"\n  Could not write report file: {e}")

    # 6. Optional: email report (set REPORT_EMAIL_TO and SMTP env vars)
    try:
        _send_report_email(report, report_file)
    except Exception as e:
        print(f"\n  Email send skipped or failed: {e}")

    print("\n" + "=" * 60)
    return report


def _send_report_email(report: dict, report_file=None):
    """
    Send daily report by email if env is set.
    Required: REPORT_EMAIL_TO (comma-separated emails)
    Optional: SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (default SMTP_USER)
    """
    to_addrs = os.getenv("REPORT_EMAIL_TO", "").strip()
    if not to_addrs:
        return
    to_list = [a.strip() for a in to_addrs.split(",") if a.strip()]
    if not to_list:
        return

    host = os.getenv("SMTP_HOST", os.getenv("REPORT_SMTP_HOST", ""))
    if not host:
        print("  Email skipped: set SMTP_HOST (or REPORT_SMTP_HOST) and REPORT_EMAIL_TO to send report by email.")
        return

    port = int(os.getenv("SMTP_PORT", os.getenv("REPORT_SMTP_PORT", "587")))
    user = os.getenv("SMTP_USER", os.getenv("REPORT_SMTP_USER", ""))
    password = os.getenv("SMTP_PASSWORD", os.getenv("REPORT_SMTP_PASSWORD", ""))
    from_addr = os.getenv("SMTP_FROM", os.getenv("REPORT_SMTP_FROM", user))

    subject = f"Aegis Daily Report — {report.get('date', '')[:10]}"
    body_lines = [
        f"Date: {report.get('date', 'N/A')}",
        f"Global accuracy: {report.get('global_stats', {}).get('global_accuracy', 0):.1f}%",
        f"Total predictions: {report.get('global_stats', {}).get('total_predictions', 0)}",
        f"Best: {report.get('global_stats', {}).get('best_symbol', {}).get('name', 'N/A')}",
        f"Worst: {report.get('global_stats', {}).get('worst_symbol', {}).get('name', 'N/A')}",
        "",
        "AI Summary:",
        (report.get("ai_summary") or "N/A")[:1500],
    ]
    body = "\n".join(body_lines)

    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email import encoders

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_list)
    msg.attach(MIMEText(body, "plain"))

    if report_file and report_file.exists():
        with open(report_file, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={report_file.name}")
            msg.attach(part)

    with smtplib.SMTP(host, port) as server:
        if user and password:
            server.starttls()
            server.login(user, password)
        server.sendmail(from_addr, to_list, msg.as_string())
    print(f"  Report emailed to {to_list}")


if __name__ == "__main__":
    generate_daily_report()
