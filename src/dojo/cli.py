"""Command Line Interface for Dojo Agent."""

import argparse
import sys
from getpass import getpass
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from dojo.config import settings
from dojo.db import DojoDatabase
from dojo.client import DojoClient, TwoFactorRequiredError
from dojo.db import DojoDatabase, get_database
from dojo.digest import DigestEngine
from dojo.mailer import Mailer

console = Console()


def get_db():
    settings.ensure_directories()
    return get_database()


def cmd_login(args: argparse.Namespace) -> None:
    """Authenticate with ClassDojo and store session cookies."""
    settings.ensure_directories()

    if args.browser:
        from dojo.browser_auth import interactive_browser_login
        success = interactive_browser_login(settings.dojo_session_file)
        if success:
            console.print("[bold green]Browser login succeeded! Session saved.[/bold green]")
        else:
            console.print("[bold red]Browser login failed or was cancelled.[/bold red]")
            sys.exit(1)
        return

    email = args.email or settings.dojo_email
    if not email:
        email = input("ClassDojo Email: ").strip()

    password = args.password or settings.dojo_password
    if not password:
        password = getpass("ClassDojo Password: ")

    client = DojoClient(session_file=settings.dojo_session_file)

    def otc_callback() -> str:
        console.print("[bold yellow]⚠️  ClassDojo requires a One-Time Verification Code.[/bold yellow]")
        console.print("[dim]Check your email for the verification code sent by ClassDojo.[/dim]")
        return input("Enter 6-digit code: ").strip()

    try:
        console.print(f"Connecting to ClassDojo as [cyan]{email}[/cyan]...")
        session_data = client.login(email, password, code_prompt=otc_callback)
        console.print("[bold green]✓ Login successful! Session stored in data/session.json[/bold green]")

        # Fetch and display user profile
        user_info = session_data.get("user") or session_data
        name = user_info.get("name") or email
        console.print(f"Logged in as: [bold]{name}[/bold]")

    except TwoFactorRequiredError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Login failed: {e}[/bold red]")
        console.print("[dim]Tip: If ClassDojo is blocking direct API login, try: dojo login --browser[/dim]")
        sys.exit(1)
    finally:
        client.close()


def cmd_sync(args: argparse.Namespace) -> None:
    """Fetch latest data from ClassDojo and store in SQLite."""
    settings.ensure_directories()
    client = DojoClient(session_file=settings.dojo_session_file)

    if not settings.dojo_session_file.exists():
        console.print("[bold red]No active session found. Please run 'dojo login' first.[/bold red]")
        sys.exit(1)

    db = get_db()

    with console.status("[bold blue]Syncing data from ClassDojo...[/bold blue]"):
        try:
            # 1. Update session & enrolled students / classes
            try:
                session_info = client.get_session_info()
                children = session_info.get("children") or []
                for ch in children:
                    ch_id = str(ch.get("id") or ch.get("_id"))
                    ch_name = ch.get("name") or "Child"
                    db.upsert_child(ch_id, ch_name)

                classes = session_info.get("classes") or []
                for cl in classes:
                    cl_id = str(cl.get("id") or cl.get("_id"))
                    cl_name = cl.get("name") or "Class"
                    db.upsert_class(cl_id, cl_name)
            except Exception as e:
                console.print(f"[dim yellow]Warning fetching session info: {e}[/dim yellow]")

            # 2. Fetch story feed
            feed_items = client.get_story_feed(limit=args.limit)
            new_feed, updated_feed = db.upsert_feed_items(feed_items)

            # 3. Fetch messages
            messages = client.get_messages()
            new_msg, updated_msg = db.upsert_messages(messages)

            # 4. Fetch events
            events = client.get_events()
            new_ev, updated_ev = db.upsert_events(events)

        except Exception as e:
            console.print(f"[bold red]Sync error: {e}[/bold red]")
            sys.exit(1)
        finally:
            client.close()

    table = Table(title="Sync Results", border_style="cyan")
    table.add_column("Category", style="bold")
    table.add_column("New Items", style="green")
    table.add_column("Updated Items", style="dim")

    table.add_row("Feed Posts / Stories", str(new_feed), str(updated_feed))
    table.add_row("Direct Messages", str(new_msg), str(updated_msg))
    table.add_row("Calendar Events", str(new_ev), str(updated_ev))

    console.print(table)
    stats = db.get_stats()
    console.print(f"[dim]Pending un-digested items: {stats['undigested_items']}[/dim]")


def cmd_recap(args: argparse.Namespace) -> None:
    """Generate anti-bloat briefing and optionally send via email."""
    db = get_db()
    data = db.get_undigested_items()

    feed_items = data["feed_items"]
    messages = data["messages"]
    events = data["events"]

    total_undigested = len(feed_items) + len(messages) + len(events)
    if total_undigested == 0 and not args.force:
        console.print("[bold green]✨ All caught up! No new items to recap.[/bold green]")
        console.print("[dim]Use --force to generate a recap anyway.[/dim]")
        return

    # Fetch children for header
    children = db.get_all_children() if hasattr(db, "get_all_children") else []

    engine = DigestEngine(gemini_api_key=settings.gemini_api_key)
    briefing = engine.synthesize(
        feed_items=feed_items,
        messages=messages,
        events=events,
        children=children
    )

    plain_text = engine.format_plain_text(briefing)
    console.print(Panel(plain_text, title="🎒 ClassDojo Daily Briefing", border_style="green"))

    mailer = Mailer(settings)
    html_content = mailer.render_html(briefing)
    preview_file = mailer.save_preview(html_content)
    console.print(f"[dim]HTML preview saved to: [cyan]{preview_file}[/cyan][/dim]")

    if args.send_email:
        to_email = args.to or settings.email_to
        if not to_email:
            console.print("[bold red]Cannot send email: No recipient email specified (set EMAIL_TO in .env or pass --to).[/bold red]")
            sys.exit(1)

        try:
            with console.status(f"[bold blue]Sending digest email to {to_email}...[/bold blue]"):
                mailer.send_digest(briefing, plain_text, to_email=to_email)
            console.print(f"[bold green]✓ Email digest successfully sent to {to_email}![/bold green]")

            # Mark items as digested in SQLite
            if not args.dry_run:
                feed_ids = [str(f["id"]) for f in feed_items]
                msg_ids = [str(m["id"]) for m in messages]
                ev_ids = [str(e["id"]) for e in events]
                db.mark_items_digested(feed_ids, msg_ids, ev_ids)
                db.record_digest(html_content, plain_text, to_email, total_undigested, "sent")
                console.print("[dim]Marked items as digested in database.[/dim]")

        except Exception as e:
            console.print(f"[bold red]Failed to send email: {e}[/bold red]")
            db.record_digest(html_content, plain_text, to_email, total_undigested, f"failed: {e}")
            sys.exit(1)


def cmd_run(args: argparse.Namespace) -> None:
    """One-shot command: Sync from ClassDojo and send email recap if new items exist."""
    console.print("[bold blue]Starting automated Dojo Agent run...[/bold blue]")

    # 1. Sync
    args.limit = getattr(args, "limit", 50)
    cmd_sync(args)

    # 2. Check undigested
    db = get_db()
    stats = db.get_stats()
    if stats["undigested_items"] == 0 and not getattr(args, "force", False):
        console.print("[bold green]No new items to recap today. Done![/bold green]")
        return

    # 3. Recap & Send Email
    args.send_email = True
    args.dry_run = False
    args.force = False
    args.to = getattr(args, "to", None)
    cmd_recap(args)


def cmd_status(args: argparse.Namespace) -> None:
    """Show current session, database, and configuration status."""
    settings.ensure_directories()
    db = get_db()
    stats = db.get_stats()

    client = DojoClient(session_file=settings.dojo_session_file)
    has_session_file = settings.dojo_session_file.exists()
    is_auth = client.is_authenticated() if has_session_file else False
    client.close()

    table = Table(title="Dojo Agent Status", border_style="blue")
    table.add_column("Component", style="bold")
    table.add_column("Status / Value")

    table.add_row("Session File", str(settings.dojo_session_file))
    table.add_row(
        "ClassDojo Authentication",
        "[green]Authenticated (Valid Session)[/green]" if is_auth else (
            "[yellow]Session file present, but expired or unverified[/yellow]" if has_session_file else "[red]Not logged in[/red]"
        )
    )
    table.add_row("Database Path", str(settings.dojo_db_path))
    table.add_row("Enrolled Children", str(stats["children_count"]))
    table.add_row("Enrolled Classes", str(stats["classes_count"]))
    table.add_row("Total Feed Posts in DB", str(stats["feed_total"]))
    table.add_row("Pending Undigested Items", str(stats["undigested_items"]))
    table.add_row("Last Sync Timestamp", str(stats["last_sync"] or "Never"))
    table.add_row("SMTP Server", f"{settings.smtp_host}:{settings.smtp_port} (User: {settings.smtp_user or 'None'})")
    table.add_row("Email Recipient", settings.email_to or "[yellow]Not configured[/yellow]")

    console.print(table)


def cmd_check_alerts(args: argparse.Namespace) -> None:
    """One-shot check for newly arrived urgent messages/announcements."""
    from dojo.monitor import MessageMonitor
    settings.ensure_directories()
    db = get_db()
    monitor = MessageMonitor(settings, db)

    console.print("[bold blue]Checking ClassDojo for incoming urgent messages...[/bold blue]")
    alerts = monitor.check_once(send_email=not args.no_email, to_email=args.to)

    if not alerts:
        console.print("[bold green]✓ No urgent alerts found.[/bold green] (Routine messages held for daily recap)")
    else:
        console.print(f"[bold red]Found {len(alerts)} urgent alert(s)![/bold red]")


def cmd_watch(args: argparse.Namespace) -> None:
    """Run real-time monitoring daemon."""
    from dojo.monitor import MessageMonitor
    settings.ensure_directories()
    db = get_db()
    monitor = MessageMonitor(settings, db)
    monitor.watch(poll_interval=args.interval, to_email=args.to)


def cmd_serve(args: argparse.Namespace) -> None:
    """Launch the Dojo Zen Web Portal & Q&A Assistant."""
    from dojo.server import run_server
    settings.ensure_directories()
    run_server(host=args.host, port=args.port)


def cmd_seed_firestore(args: argparse.Namespace) -> None:
    """Migrate local SQLite data and session cookies to Google Cloud Firestore."""
    from dojo.firestore_db import FirestoreDojoDatabase
    import json

    sqlite_db = DojoDatabase(settings.dojo_db_path)
    fdb = FirestoreDojoDatabase(project=settings.google_cloud_project)

    console.print(f"[bold blue]Connecting to Firestore on project '[cyan]{settings.google_cloud_project}[/cyan]'...[/bold blue]")

    with sqlite_db.get_connection() as conn:
        child_rows = conn.execute("SELECT * FROM children").fetchall()
        for c in child_rows:
            fdb.upsert_child(c["id"], c["name"], c["grade"], c["avatar_url"])
        console.print(f"[green]✓ Migrated {len(child_rows)} children to Firestore.[/green]")

        class_rows = conn.execute("SELECT * FROM classes").fetchall()
        for cl in class_rows:
            fdb.upsert_class(cl["id"], cl["name"], cl["teacher_name"], cl["child_id"])
        console.print(f"[green]✓ Migrated {len(class_rows)} classes to Firestore.[/green]")

        feeds = [dict(r) for r in conn.execute("SELECT * FROM feed_items").fetchall()]
        new_f, upd_f = fdb.upsert_feed_items(feeds)
        console.print(f"[green]✓ Migrated {len(feeds)} feed items ({new_f} new, {upd_f} updated) to Firestore.[/green]")

        msgs = [dict(r) for r in conn.execute("SELECT * FROM messages").fetchall()]
        new_m, upd_m = fdb.upsert_messages(msgs)
        console.print(f"[green]✓ Migrated {len(msgs)} messages ({new_m} new, {upd_m} updated) to Firestore.[/green]")

        events = [dict(r) for r in conn.execute("SELECT * FROM events").fetchall()]
        new_e, upd_e = fdb.upsert_events(events)
        console.print(f"[green]✓ Migrated {len(events)} events ({new_e} new, {upd_e} updated) to Firestore.[/green]")

    if settings.dojo_session_file.exists():
        cookie_dict = json.loads(settings.dojo_session_file.read_text())
        fdb.save_session(cookie_dict)
        console.print(f"[green]✓ Migrated session cookies ({len(cookie_dict)} cookies) to Firestore.[/green]")

def cmd_analyze_images(args: argparse.Namespace) -> None:
    """Analyze image attachments in feed posts using Gemini Multimodal OCR."""
    import json
    from dojo.vision import analyze_feed_attachments

    db = get_db()
    client = DojoClient(session_file=settings.dojo_session_file)
    cookies = {c.name: c.value for c in client.client.cookies.jar} if hasattr(client, "client") and client.client.cookies else {}

    all_items = db.get_all_feed_items()
    candidates = []
    for it in all_items:
        # Check attachments
        att_raw = it.get("attachments") or it.get("attachments_json")
        has_att = False
        if att_raw:
            try:
                atts = json.loads(att_raw) if isinstance(att_raw, str) else att_raw
                if atts and len(atts) > 0:
                    has_att = True
            except Exception:
                has_att = False

        if not has_att:
            continue

        # Check if already has OCR
        ocr_raw = it.get("ocr_json") or it.get("ocr_data")
        if not getattr(args, "all", False) and ocr_raw:
            try:
                parsed_ocr = json.loads(ocr_raw) if isinstance(ocr_raw, str) else ocr_raw
                if parsed_ocr:
                    continue
            except Exception:
                pass

        candidates.append(it)

    limit = args.limit or len(candidates)
    to_process = candidates[:limit]

    if not to_process:
        console.print("[green]✓ All posts with image attachments have already been analyzed.[/green]")
        return

    console.print(f"[bold cyan]Scanning {len(to_process)} feed posts with Gemini Multimodal OCR...[/bold cyan]\n")

    for idx, item in enumerate(to_process, 1):
        item_id = item["id"]
        author = item.get("author_name") or "School"
        header = item.get("header") or item.get("content_text", "")[:40]
        console.print(f"[dim][{idx}/{len(to_process)}][/dim] [bold]{author}[/bold] - [dim]{header}...[/dim]")

        ocr_results = analyze_feed_attachments(item, cookies=cookies)
        if ocr_results:
            db.update_item_ocr(item_id, ocr_results)
            for res in ocr_results:
                if res.get("has_text"):
                    console.print(f"  [green]✓ Found text:[/green] {res.get('summary')}")
                    if res.get("dates"):
                        console.print(f"    [cyan]Dates:[/cyan] {', '.join(res['dates'])}")
                    if res.get("action_items"):
                        console.print(f"    [yellow]Actions:[/yellow] {', '.join(res['action_items'])}")
                else:
                    console.print(f"  [dim]Casual photo (no text)[/dim]")
        else:
            console.print("  [dim]No downloadable attachments processed.[/dim]")

    console.print(f"\n[bold green]✓ Completed image analysis for {len(to_process)} posts.[/bold green]")


def cmd_mcp(args: argparse.Namespace) -> None:
    """Run the Model Context Protocol (MCP) server for ClassDojo."""
    from dojo.mcp_server import run_mcp
    transport = getattr(args, "transport", "stdio")
    run_mcp(transport=transport)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dojo",
        description="ClassDojo Anti-Bloat Agent & Email Digest Engine"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # login
    p_login = subparsers.add_parser("login", help="Log in to ClassDojo and store session")
    p_login.add_argument("--email", help="ClassDojo account email")
    p_login.add_argument("--password", help="ClassDojo account password")
    p_login.add_argument("--browser", action="store_true", help="Use Playwright browser window to log in")
    p_login.set_defaults(func=cmd_login)

    # sync
    p_sync = subparsers.add_parser("sync", help="Pull latest posts, messages, and events into SQLite")
    p_sync.add_argument("--limit", type=int, default=50, help="Maximum feed items to fetch")
    p_sync.set_defaults(func=cmd_sync)

    # recap
    p_recap = subparsers.add_parser("recap", help="Generate anti-bloat briefing")
    p_recap.add_argument("--send-email", action="store_true", help="Send the recap to configured email inbox")
    p_recap.add_argument("--to", help="Override recipient email address")
    p_recap.add_argument("--dry-run", action="store_true", help="Do not mark items as digested in the database")
    p_recap.add_argument("--force", action="store_true", help="Generate recap even if no new items exist")
    p_recap.set_defaults(func=cmd_recap)

    # run
    p_run = subparsers.add_parser("run", help="One-shot sync + email recap (for daily cron)")
    p_run.add_argument("--limit", type=int, default=50, help="Maximum feed items to fetch")
    p_run.add_argument("--to", help="Override recipient email address")
    p_run.add_argument("--force", action="store_true", help="Send email even if no new items exist")
    p_run.set_defaults(func=cmd_run)

    # status
    p_status = subparsers.add_parser("status", help="Show current authentication and database statistics")
    p_status.set_defaults(func=cmd_status)

    # check-alerts
    p_alerts = subparsers.add_parser("check-alerts", help="Check for newly arrived urgent messages/announcements")
    p_alerts.add_argument("--to", help="Override recipient email address")
    p_alerts.add_argument("--no-email", action="store_true", help="Evaluate and print alerts without sending email")
    p_alerts.set_defaults(func=cmd_check_alerts)

    # watch
    p_watch = subparsers.add_parser("watch", help="Run real-time monitoring daemon")
    p_watch.add_argument("--interval", type=int, default=300, help="Polling interval in seconds (default: 300s)")
    p_watch.add_argument("--to", help="Override recipient email address")
    p_watch.set_defaults(func=cmd_watch)

    # serve
    p_serve = subparsers.add_parser("serve", help="Launch the Dojo Zen Web Portal & Q&A Assistant")
    p_serve.add_argument("--host", default="0.0.0.0", help="Host address to bind (default: 0.0.0.0 for local network access)")
    p_serve.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    p_serve.set_defaults(func=cmd_serve)

    # seed-firestore
    p_seed = subparsers.add_parser("seed-firestore", help="Migrate local SQLite data and session to Cloud Firestore")
    p_seed.set_defaults(func=cmd_seed_firestore)

    # mcp
    p_mcp = subparsers.add_parser("mcp", help="Run Model Context Protocol (MCP) server for AI assistants")
    p_mcp.add_argument("--transport", choices=["stdio", "sse"], default="stdio", help="MCP transport protocol (default: stdio)")
    p_mcp.set_defaults(func=cmd_mcp)

    # analyze-images
    p_analyze = subparsers.add_parser("analyze-images", help="Scan feed attachments with Gemini Multimodal OCR")
    p_analyze.add_argument("--limit", type=int, default=10, help="Maximum items to analyze (default: 10)")
    p_analyze.add_argument("--all", action="store_true", help="Re-analyze items that already have OCR data")
    p_analyze.set_defaults(func=cmd_analyze_images)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
