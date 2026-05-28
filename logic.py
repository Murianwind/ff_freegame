# -*- coding: utf-8 -*-
import threading
import traceback

import requests
from flask import jsonify, render_template
from plugin import PluginModuleBase
from framework import F, Job, scheduler

from .model import ModelFetchLog, ModelFreeGameItem, ModelSetting
from . import scraper
from .setup import P


logger = P.logger
package_name = P.package_name
_fetch_lock = threading.Lock()

SOURCE_LABELS = {
    "epic": "Epic",
    "steam": "Steam",
    "gog": "GOG",
    "indiegala": "IndieGala",
    "stove": "STOVE",
    "cheapshark": "CheapShark",
}
def _truthy(value):
    return str(value).lower() == "true"


def _enabled_sources():
    enabled = []
    for source in SOURCE_LABELS:
        if _truthy(ModelSetting.get(f"source_{source}_enabled")):
            enabled.append(source)
    return enabled


def _split_source_payload(results):
    grouped = {source: [] for source in SOURCE_LABELS}
    for source, items in (results or {}).items():
        normalized_source = "indiegala" if source == "indiegala_free" else source
        if normalized_source not in grouped:
            continue
        for item in items or []:
            is_free = bool(item.get("is_free_period")) or float(item.get("current_price") or 0) == 0
            if not is_free:
                continue
            if str(item.get("platform") or "") not in ["", normalized_source]:
                continue
            item["platform"] = normalized_source
            grouped[normalized_source].append(item)
    return grouped


def _discord_send(webhook_url, games):
    lines = []
    for game in games[:10]:
        title = game.get("title") or "Unknown"
        platform = SOURCE_LABELS.get(game.get("platform"), game.get("platform"))
        store_url = game.get("store_url") or ""
        score = int(game.get("metacritic_score") or 0)
        line = f"**{title}** ({platform})"
        if score > 0:
            line += f" · MC {score}"
        if store_url:
            line += f"\n{store_url}"
        lines.append(line)
    content = "**무료 게임 알림**\n\n" + "\n\n".join(lines)
    requests.post(webhook_url, json={"content": content}, timeout=10).raise_for_status()


def _telegram_send(bot_token, chat_id, games):
    lines = []
    for game in games[:10]:
        title = game.get("title") or "Unknown"
        platform = SOURCE_LABELS.get(game.get("platform"), game.get("platform"))
        store_url = game.get("store_url") or ""
        score = int(game.get("metacritic_score") or 0)
        line = f"<b>{title}</b> ({platform})"
        if score > 0:
            line += f" · MC {score}"
        if store_url:
            line += f"\n{store_url}"
        lines.append(line)
    requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": "무료 게임 알림\n\n" + "\n\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=10,
    ).raise_for_status()


class Logic(PluginModuleBase):
    instance = None
    db_default = {
        "auto_start": "False",
        "auto_interval": "0 */2 * * *",
        "notify_discord_webhook": "",
        "notify_telegram_bot_token": "",
        "notify_telegram_chat_id": "",
        "notify_enabled": "False",
        "source_epic_enabled": "True",
        "source_steam_enabled": "True",
        "source_gog_enabled": "True",
        "source_indiegala_enabled": "True",
        "source_stove_enabled": "True",
        "source_cheapshark_enabled": "True",
    }

    def __init__(self, PM):
        super().__init__(PM, name="main", first_menu="setting")
        Logic.instance = self

    def plugin_load(self):
        ModelFreeGameItem.ensure_schema()
        if _truthy(ModelSetting.get("auto_start")):
            self.scheduler_start()

    def process_menu(self, sub, req):
        arg = ModelSetting.to_dict()
        arg["package_name"] = package_name
        arg["scheduler"] = str(F.scheduler.is_include(package_name))
        arg["is_running"] = str(F.scheduler.is_running(package_name))
        arg["source_labels"] = SOURCE_LABELS
        arg["platform_counts"] = ModelFreeGameItem.get_platform_counts()
        if sub == "list":
            return render_template("ff_freegame_main_list.html", arg=arg)
        if sub == "log":
            return render_template("log.html", package=package_name)
        return render_template("ff_freegame_main_setting.html", arg=arg)

    def process_ajax(self, sub, req):
        try:
            if sub == "setting_save":
                ret, _ = ModelSetting.setting_save(req)
                if F.scheduler.is_include(package_name):
                    self.scheduler_stop()
                    self.scheduler_start()
                elif _truthy(ModelSetting.get("auto_start")):
                    self.scheduler_start()
                ret["ret"] = "success"
                return jsonify(ret)
            if sub == "scheduler_toggle":
                if req.form["scheduler"] == "true":
                    self.scheduler_start()
                else:
                    self.scheduler_stop()
                return jsonify({"ret": "success"})
            if sub == "execute_once":
                Logic.execute_once()
                return jsonify({"ret": "success"})
            if sub == "web_list":
                ModelFreeGameItem.ensure_schema()
                return jsonify(ModelFreeGameItem.web_list(req))
            if sub == "platform_counts":
                return jsonify({"ret": "success", "data": ModelFreeGameItem.get_platform_counts()})
            return jsonify({"ret": "error", "log": f"unsupported ajax: {sub}"})
        except Exception as e:
            logger.error("Exception:%s", e)
            logger.error(traceback.format_exc())
            return jsonify({"ret": "error", "log": str(e)})

    def scheduler_start(self):
        try:
            interval = ModelSetting.get("auto_interval") or "0 */2 * * *"
            if F.scheduler.is_include(package_name):
                scheduler.remove_job(package_name)
            job = Job(package_name, package_name, interval, Logic.execute_once, "FreeGame fetch", True)
            scheduler.add_job_instance(job)
            logger.info("FreeGame scheduler registered: %s", interval)
        except Exception as e:
            logger.error("Exception:%s", e)
            logger.error(traceback.format_exc())

    def scheduler_stop(self):
        try:
            scheduler.remove_job(package_name)
        except Exception as e:
            logger.error("Exception:%s", e)
            logger.error(traceback.format_exc())

    @staticmethod
    def execute_once():
        threading.Thread(target=Logic.scheduler_function_static, daemon=True).start()

    @staticmethod
    def scheduler_function_static():
        logic = Logic.instance
        if logic is None:
            logger.error("FreeGame scheduler skipped: Logic instance is not initialized")
            return
        with F.app.app_context():
            logic.scheduler_function()

    def scheduler_function(self):
        if not _fetch_lock.acquire(blocking=False):
            logger.info("FreeGame fetch skipped: already running")
            return
        try:
            logger.info("FreeGame scheduled fetch started")
            ModelFreeGameItem.ensure_schema()
            enabled_sources = set(_enabled_sources())
            results = scraper.fetch_all()
            grouped = _split_source_payload(results)
            fresh_free_games = []

            for legacy_source in ["humble", "fanatical", "gmg", "directgames"]:
                ModelFreeGameItem.replace_source_items(legacy_source, [])

            for source, items in grouped.items():
                if source not in enabled_sources:
                    continue
                ModelFreeGameItem.replace_source_items(source, items)
                ModelFetchLog(source, "ok", "", len(items)).save()
                logger.info("FreeGame source=%s saved=%d", source, len(items))
                fresh_free_games.extend(items)

            disabled_sources = [source for source in SOURCE_LABELS if source not in enabled_sources]
            if disabled_sources:
                ModelFreeGameItem.delete_not_in_sources(enabled_sources)

            logger.info("FreeGame fetch completed: free_candidates=%d enabled_sources=%d", len(fresh_free_games), len(enabled_sources))
            ModelFetchLog("all", "ok", f"free_candidates={len(fresh_free_games)} enabled_sources={len(enabled_sources)}", len(fresh_free_games)).save()
            self._notify(fresh_free_games)
        except Exception as e:
            logger.error("Exception:%s", e)
            logger.error(traceback.format_exc())
            ModelFetchLog("all", "error", str(e), 0).save()
        finally:
            _fetch_lock.release()

    def _notify(self, games):
        if _truthy(ModelSetting.get("notify_enabled")) is False:
            return
        targets = list(games or [])
        if len(targets) == 0:
            return
        discord_webhook = ModelSetting.get("notify_discord_webhook")
        telegram_bot_token = ModelSetting.get("notify_telegram_bot_token")
        telegram_chat_id = ModelSetting.get("notify_telegram_chat_id")
        if discord_webhook:
            try:
                _discord_send(discord_webhook, targets)
                logger.info("FreeGame Discord notification sent: %d", min(len(targets), 10))
            except Exception as e:
                logger.error("FreeGame Discord notification failed: %s", e)
        if telegram_bot_token and telegram_chat_id:
            try:
                _telegram_send(telegram_bot_token, telegram_chat_id, targets)
                logger.info("FreeGame Telegram notification sent: %d", min(len(targets), 10))
            except Exception as e:
                logger.error("FreeGame Telegram notification failed: %s", e)
