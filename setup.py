import traceback

from plugin import *  # noqa


setting = {
    "filepath": __file__,
    "use_db": True,
    "use_default_setting": True,
    "home_module": "setting",
    "menu": {
        "uri": __package__,
        "name": "FreeGame",
        "list": [
            {"uri": "setting", "name": "설정"},
            {"uri": "list", "name": "목록"},
            {"uri": "log", "name": "로그"},
        ],
    },
    "setting_menu": None,
    "default_route": "single",
}

P = create_plugin_instance(setting)

try:
    from .logic import Logic

    P.set_module_list([Logic])
except Exception as e:
    P.logger.error(f"Exception:{str(e)}")
    P.logger.error(traceback.format_exc())
