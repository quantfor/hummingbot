import asyncio
import json
import unittest
from typing import Awaitable
from unittest.mock import AsyncMock, patch

from aioresponses import aioresponses

from hummingbot.connector.exchange.kucoin.kucoin_api_user_stream_data_source import KucoinAPIUserStreamDataSource
from hummingbot.connector.exchange.kucoin.kucoin_auth import KucoinAuth
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.connector.exchange.kucoin import kucoin_constants as CONSTANTS
from test.hummingbot.connector.network_mocking_assistant import NetworkMockingAssistant


class TestKucoinAPIUserStreamDataSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.ev_loop = asyncio.get_event_loop()
        cls.base_asset = "COINALPHA"
        cls.quote_asset = "HBOT"
        cls.trading_pair = f"{cls.base_asset}-{cls.quote_asset}"
        cls.api_key = "someKey"
        cls.api_passphrase = "somePassPhrase"
        cls.api_secret_key = "someSecretKey"

    def setUp(self) -> None:
        super().setUp()
        self.throttler = AsyncThrottler(CONSTANTS.RATE_LIMITS)
        self.auth = KucoinAuth(self.api_key, self.api_passphrase, self.api_secret_key)
        self.data_source = KucoinAPIUserStreamDataSource(self.throttler, self.auth)
        self.mocking_assistant = NetworkMockingAssistant()

    def async_run_with_timeout(self, coroutine: Awaitable, timeout: int = 1):
        ret = self.ev_loop.run_until_complete(asyncio.wait_for(coroutine, timeout))
        return ret

    @staticmethod
    def get_listen_key_mock():
        listen_key = {
            "code": "200000",
            "data": {
                "token": "someToken",
                "instanceServers": [
                    {
                        "endpoint": "wss://someEndpoint",
                        "encrypt": True,
                        "protocol": "websocket",
                        "pingInterval": 18000,
                        "pingTimeout": 10000,
                    }
                ]
            }
        }
        return listen_key

    @aioresponses()
    def test_get_listen_key_raises(self, mock_api):
        url = CONSTANTS.BASE_PATH_URL + CONSTANTS.PRIVATE_WS_DATA_PATH_URL
        mock_api.post(url, status=500)

        with self.assertRaises(IOError):
            self.async_run_with_timeout(self.data_source.get_listen_key())

    @aioresponses()
    def test_get_listen_key(self, mock_api):
        url = CONSTANTS.BASE_PATH_URL + CONSTANTS.PRIVATE_WS_DATA_PATH_URL
        resp = self.get_listen_key_mock()
        mock_api.post(url, body=json.dumps(resp))

        ret = self.async_run_with_timeout(self.data_source.get_listen_key())

        self.assertEqual(ret, resp)  # shallow comparison ok

    @aioresponses()
    @patch("websockets.connect", new_callable=AsyncMock)
    def test_listen_to_user_stream(self, mock_api, ws_connect_mock):
        url = CONSTANTS.BASE_PATH_URL + CONSTANTS.PRIVATE_WS_DATA_PATH_URL
        resp = self.get_listen_key_mock()
        mock_api.post(url, body=json.dumps(resp))

        ws_connect_mock.return_value = self.mocking_assistant.create_websocket_mock()
        msg = "someMsg"
        msg_queue = asyncio.Queue()
        self.mocking_assistant.add_websocket_text_message(ws_connect_mock.return_value, json.dumps(msg))

        self.ev_loop.create_task(self.data_source.listen_for_user_stream(self.ev_loop, msg_queue))
        self.mocking_assistant.run_until_all_text_messages_delivered(ws_connect_mock.return_value)

        self.assertTrue(not msg_queue.empty())

        queued = msg_queue.get_nowait()

        self.assertEqual(msg, queued)

    @aioresponses()
    @patch("websockets.connect", new_callable=AsyncMock)
    def test_listen_for_user_stream_closes_ws_on_exception(self, mock_api, ws_connect_mock):
        url = CONSTANTS.BASE_PATH_URL + CONSTANTS.PRIVATE_WS_DATA_PATH_URL
        resp = self.get_listen_key_mock()
        mock_api.post(url, body=json.dumps(resp))
        raised_event = asyncio.Event()

        async def raise_exception():
            raised_event.set()
            raise IOError

        ws_connect_mock.return_value = self.mocking_assistant.create_websocket_mock()
        ws_connect_mock.return_value.recv.side_effect = raise_exception

        self.ev_loop.create_task(self.data_source.listen_for_user_stream(self.ev_loop, asyncio.Queue()))
        self.async_run_with_timeout(coroutine=raised_event.wait())

        ws_connect_mock.return_value.close.assert_called()
