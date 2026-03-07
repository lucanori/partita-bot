import importlib
import sys


def _reload_wsgi():
    if "wsgi" in sys.modules:
        del sys.modules["wsgi"]
    return importlib.import_module("wsgi")


def test_wsgi_exports_application():
    wsgi_module = _reload_wsgi()
    assert hasattr(wsgi_module, "application")


def test_wsgi_application_is_flask_app():
    wsgi_module = _reload_wsgi()
    from flask import Flask

    assert isinstance(wsgi_module.application, Flask)
