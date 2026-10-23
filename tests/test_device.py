from overdrive import device


def test_ally_x_detected():
    assert device.is_ally(lambda: "ROG Xbox Ally X RC73XA_RC73XA") is True


def test_rc71_detected():
    assert device.is_ally(lambda: "ROG Ally RC71L") is True


def test_other_device():
    assert device.is_ally(lambda: "Legion Go 8APU1") is False


def test_read_failure_is_unsupported():
    def boom():
        raise OSError("no key")

    assert device.is_ally(boom) is False
    assert device.product_name(boom) == ""


def test_get_support_shape(monkeypatch):
    import main

    monkeypatch.setattr(main.device, "product_name", lambda read_product=None: "ROG Ally RC71L")
    import asyncio

    assert asyncio.run(main.Plugin().get_support()) == {"supported": True, "product": "ROG Ally RC71L"}
