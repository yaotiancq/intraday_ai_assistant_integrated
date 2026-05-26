from app.utils.console import configure_utf8_stdio


def test_configure_utf8_stdio_does_not_raise():
    configure_utf8_stdio()
