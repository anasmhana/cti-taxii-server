import base64
import copy
import json
import sys
import time
import unittest
import uuid

import pytest
import six

from medallion import (application_instance, register_blueprints, set_config,
                       test)
from medallion.views import MEDIA_TYPE_TAXII_V21
import requests
from requests.auth import HTTPBasicAuth

from .base_test import TaxiiTest

if sys.version_info < (3, 3, 0):
    import mock
else:
    from unittest import mock

BUNDLE = {
    "id": "bundle--8fab937e-b694-11e3-b71c-0800271e87d2",
    "objects": [
    ],
    "spec_version": "2.0",
    "type": "bundle",
}

API_OBJECT = {
    "created": "2017-01-27T13:49:53.935Z",
    "id": "indicator--%s",
    "labels": [
        "url-watchlist",
    ],
    "modified": "2017-01-27T13:49:53.935Z",
    "name": "Malicious site hosting downloader",
    "pattern": "[url:value = 'http://x4z9arb.cn/5000']",
    "type": "indicator",
    "valid_from": "2017-01-27T13:49:53.935382Z",
}


class MemoryTestServer(TaxiiTest):

    def __init__(self):
        self.type = "memory"
        self.setUp()


class MongoTestServer(TaxiiTest):

    def __init__(self):
        self.type = "mongo"
        self.setUp()


class TestDoubleTAXIIServer(unittest.TestCase):

    backends = [MemoryTestServer(), MongoTestServer()]

    """
    def test_backends(self):
        backends = [MongoTestServer(), MemoryTestServer()]
        for b in backends:
            b.setUp()
        object_methods = [method_name for method_name in dir(self)
                          if method_name[:4] == "func"]
        for method_name in object_methods:
            results = []
            test_method = getattr(self, method_name)
            for backend in backends:
                results.append(test_method(backend))
            assert len(set(results)) == 1"""

    @staticmethod
    def load_json_response(response):
        if isinstance(response, bytes):
            response = response.decode()
        io = six.StringIO(response)
        x = json.load(io)
        return x

    def test_server_discovery(self):
        results = []
        for backend in self.backends:
            results.append(backend.client.get(test.DISCOVERY_EP, headers=backend.headers))

        for r in results:
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content_type, MEDIA_TYPE_TAXII_V21)
            server_info = self.load_json_response(r.data)
            assert server_info["api_roots"][0] == "http://localhost:5000/api1/"

        #assert len(set(results)) == 1


    def test_get_api_root_information(self):
        results = []
        for backend in self.backends:
            results.append(backend.client.get(test.API_ROOT_EP, headers=backend.headers))

        for r in results:
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content_type, MEDIA_TYPE_TAXII_V21)
            api_root_metadata = self.load_json_response(r.data)
            assert api_root_metadata["title"] == "Malware Research Group"

        #assert len(set(results)) == 1


class TestTAXIIServerWithMockBackend(unittest.TestCase):

    def setUp(self):
        self.app = application_instance
        self.app_context = application_instance.app_context()
        self.app_context.push()
        self.app.testing = True
        register_blueprints(self.app)
        self.configuration = {
            "backend": {
                "module": "medallion.backends.mongodb_backend",
                "module_class": "MongoBackend",
                "uri": "mongodb://localhost:27017/",
                "default_page_size": 20,
            },
            "users": {
                "admin": "Password0",
            },
            "taxii": {
                "max_page_size": 20,
            },
        }
        self.client = application_instance.test_client()
        set_config(self.app, "users", self.configuration)
        set_config(self.app, "taxii", self.configuration)
        encoded_auth = 'Basic ' + base64.b64encode(b"admin:Password0").decode("ascii")
        self.auth = {'Authorization': encoded_auth}

    def tearDown(self):
        self.app_context.pop()

    @staticmethod
    def load_json_response(response):
        if isinstance(response, bytes):
            response = response.decode()
        io = six.StringIO(response)
        return json.load(io)

    @mock.patch('medallion.backends.base.Backend')
    @pytest.mark.xfail(reason="Test needs to be updated")
    def test_responses_include_range_headers(self, mock_backend):
        """ This test confirms that the expected endpoints are returning the Accept-Ranges
        HTTP header as per section 3.4 of the specification """

        self.app.medallion_backend = mock_backend()

        # ------------- BEGIN: test collection endpoint ------------- #
        mock_backend.return_value.get_collections.return_value = (10, {'collections': []})
        r = self.client.get(test.COLLECTIONS_EP, headers=self.auth)

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, MEDIA_TYPE_TAXII_V21)
        self.assertIsNotNone(r.headers.get('Accept-Ranges', None))

        # ------------- END: test collection endpoint ------------- #
        # ------------- BEGIN: test manifests endpoint ------------- #
        mock_backend.return_value.get_object_manifest.return_value = (10, {'objects': []})
        r = self.client.get(
            test.MANIFESTS_EP,
            headers=self.auth,
        )

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, MEDIA_TYPE_TAXII_V21)
        self.assertIsNotNone(r.headers.get('Accept-Ranges', None))

        # ------------- END: test manifests endpoint ------------- #
        # ------------- BEGIN: test objects endpoint ------------- #
        mock_backend.return_value.get_objects.return_value = (10, {'objects': []})
        r = self.client.get(
            test.GET_OBJECTS_EP,
            headers=self.auth,
        )

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, MEDIA_TYPE_TAXII_V21)
        self.assertIsNotNone(r.headers.get('Accept-Ranges', None))

        # ------------- END: test objects endpoint ------------- #

    @mock.patch('medallion.backends.base.Backend')
    @pytest.mark.xfail(reason="Test needs to be updated")
    def test_response_status_headers_for_large_responses(self, mock_backend):
        """ This test confirms that the expected endpoints are returning the Accept-Ranges and
        Content-Range headers as well as a HTTP 206 for large responses. Refer section 3.4.3
        of the specification """

        self.app.medallion_backend = mock_backend()
        page_size = self.configuration['backend']['default_page_size']

        # ------------- BEGIN: test small result set - no range required ------------- #
        # set up mock backend to return test objects
        response = copy.deepcopy(BUNDLE)
        for i in range(0, 10):
            new_id = "indicator--%s" % uuid.uuid4()
            obj = copy.deepcopy(API_OBJECT)
            obj['id'] = new_id
            response['objects'].append(obj)
        mock_backend.return_value.get_objects.return_value = (10, response)

        r = self.client.get(test.GET_OBJECT_EP, headers=self.auth)

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, MEDIA_TYPE_TAXII_V21)
        self.assertIsNotNone(r.headers.get('Accept-Ranges', None))

        # ------------- END: test small result set ------------- #
        # ------------- BEGIN: test large result set ------------- #
        # set up mock backend to return larger set of test objects
        response = copy.deepcopy(BUNDLE)
        for i in range(0, 20):
            new_id = "indicator--%s" % uuid.uuid4()
            obj = copy.deepcopy(API_OBJECT)
            obj['id'] = new_id
            response['objects'].append(obj)
        mock_backend.return_value.get_objects.return_value = (100, response)

        r = self.client.get(test.GET_OBJECT_EP, headers=self.auth)
        objs = self.load_json_response(r.data)

        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.content_type, MEDIA_TYPE_TAXII_V21)
        self.assertIsNotNone(r.headers.get('Accept-Ranges', None))
        self.assertIsNotNone(r.headers.get('Content-Range', None))
        self.assertEqual(r.headers.get('Content-Range'), 'items 0-{}/100'.format(page_size - 1))
        self.assertEqual(len(objs['objects']), page_size)

        # ------------- END: test large result set ------------- #

    @mock.patch('medallion.backends.base.Backend')
    @pytest.mark.xfail(reason="Test needs to be updated")
    def test_bad_range_request(self, mock_backend):
        """ This test should return a HTTP 416 for a range request that cannot be satisfied. Refer 3.4.2 in
        the TAXII specification. """

        self.app.medallion_backend = mock_backend()

        # Set up the backend to return the total number of results = 10. Actual results not important
        # for this test so an empty set is returned.
        mock_backend.return_value.get_objects.return_value = (10, {'objects': []})

        headers = {
            'Authorization': self.auth['Authorization'],
            'Range': 'items 100-199',
        }
        r = self.client.get(test.GET_OBJECT_EP, headers=headers)

        self.assertEqual(r.status_code, 416)
        self.assertEqual(r.headers.get('Content-Range'), 'items */10')

    @mock.patch('medallion.backends.base.Backend')
    @pytest.mark.xfail(reason="Test needs to be updated")
    def test_invalid_range_request(self, mock_backend):
        """ This test should return a HTTP 400 with a message that the request contains a malformed
        range request header. """

        self.app.medallion_backend = mock_backend()

        # Set up the backend to return the total number of results = 10. Actual results not important
        # for this test so an empty set is returned.
        mock_backend.return_value.get_objects.return_value = (10, {'objects': []})

        headers = {
            'Authorization': self.auth['Authorization'],
            'Range': 'items x-199',
        }
        r = self.client.get(test.GET_OBJECT_EP, headers=headers)

        self.assertEqual(r.status_code, 400)

    @mock.patch('medallion.backends.base.Backend')
    @pytest.mark.xfail(reason="Test needs to be updated")
    def test_content_range_header_empty_response(self, mock_backend):
        """ This test checks that the Content-Range header is correctly formed for queries that return
        an empty (zero record) response. """
        self.app.medallion_backend = mock_backend()

        # Set up the backend to return the total number of results = 0.
        mock_backend.return_value.get_objects.return_value = (0, {'objects': []})

        headers = {
            'Authorization': self.auth['Authorization'],
            'Range': 'items 0-10',
        }
        r = self.client.get(test.GET_OBJECT_EP, headers=headers)

        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.headers.get('Content-Range'), 'items 0-0/0')
