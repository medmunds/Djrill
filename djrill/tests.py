from mock import patch

from django.conf import settings
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.utils import simplejson as json

from djrill.mail import DjrillMessage

class DjrillBackendMockAPITestCase(TestCase):
    """TestCase that sets up the Djrill EmailBackend with a mocked Mandrill API"""

    class MockResponse:
        """requests.post return value mock sufficient for DjrillBackend"""
        def __init__(self, status_code=200):
            self.status_code = status_code

    def setUp(self):
        self.patch = patch('requests.post')
        self.mock_post = self.patch.start()
        self.mock_post.return_value = self.MockResponse()

        settings.MANDRILL_API_KEY = "FAKE_API_KEY_FOR_TESTING"

        self.original_email_backend = settings.EMAIL_BACKEND # this will be Django's locmem EmailBackend during tests
        settings.EMAIL_BACKEND = "djrill.mail.backends.djrill.DjrillBackend"

    def tearDown(self):
        self.patch.stop()
        settings.EMAIL_BACKEND = self.original_email_backend

    def get_api_call_data(self):
        """Returns the data posted to the Mandrill API, or fails test if API wasn't called"""
        if self.mock_post.call_args is None:
            raise AssertionError("Mandrill API was not called")
        (args, kwargs) = self.mock_post.call_args
        if 'data' not in kwargs:
            raise AssertionError("requests.post was called without data kwarg -- "
                "Maybe tests need to be updated for backend changes?")
        return json.loads(kwargs['data'])


class DjrillBackendTests(DjrillBackendMockAPITestCase):
    """Test Djrill's support for Django mail wrappers"""

    def test_send_mail(self):
        mail.send_mail('Subject here', 'Here is the message.', 'from@example.com',
            ['to@example.com'], fail_silently=False)
        data = self.get_api_call_data()
        self.assertEqual(data['message']['subject'], "Subject here")
        self.assertEqual(data['message']['text'], "Here is the message.")
        self.assertEqual(data['message']['from_email'], "from@example.com")
        self.assertEqual(len(data['message']['to']), 1)
        self.assertEqual(data['message']['to'][0]['email'], "to@example.com")

    def test_missing_api_key(self):
        del settings.MANDRILL_API_KEY
        with self.assertRaises(ImproperlyConfigured):
            mail.send_mail('Subject', 'Message', 'from@example.com', ['to@example.com'])

    def test_name_addr(self):
        """Make sure RFC2822 name-addr format (with display-name) is allowed for both from and to"""
        mail.send_mail('Subject', 'Message', 'From Name <from@example.com>',
            ['Recipient #1 <to1@example.com>', 'to2@example.com'])
        data = self.get_api_call_data()
        self.assertEqual(data['message']['from_name'], "From Name")
        self.assertEqual(data['message']['from_email'], "from@example.com")
        self.assertEqual(len(data['message']['to']), 2)
        self.assertEqual(data['message']['to'][0]['name'], "Recipient #1")
        self.assertEqual(data['message']['to'][0]['email'], "to1@example.com")
        self.assertEqual(data['message']['to'][1]['name'], "")
        self.assertEqual(data['message']['to'][1]['email'], "to2@example.com")

    def test_email_message(self):
        email = mail.EmailMessage('Subject', 'Body goes here', 'from@example.com',
            ['to1@example.com', 'Also To <to2@example.com>'],
            bcc=['bcc1@example.com', 'Also BCC <bcc2@example.com>'],
            cc=['cc1@example.com', 'Also CC <cc2@example.com>'],
            headers={'Reply-To': 'another@example.com', 'X-MyHeader': 'my value', 'Errors-To': 'silently stripped'})
        email.send()
        data = self.get_api_call_data()
        self.assertEqual(data['message']['subject'], "Subject")
        self.assertEqual(data['message']['text'], "Body goes here")
        self.assertEqual(data['message']['from_email'], "from@example.com")
        self.assertEqual(data['message']['headers'], { 'Reply-To': 'another@example.com', 'X-MyHeader': 'my value' })
        # Mandrill doesn't have a notion of cc, and only allows a single bcc.
        # Djrill just treats cc and bcc as though they were "to" addresses,
        # which may or may not be what you want.
        self.assertEqual(len(data['message']['to']), 6)
        self.assertEqual(data['message']['to'][0]['email'], "to1@example.com")
        self.assertEqual(data['message']['to'][1]['email'], "to2@example.com")
        self.assertEqual(data['message']['to'][2]['email'], "cc1@example.com")
        self.assertEqual(data['message']['to'][3]['email'], "cc2@example.com")
        self.assertEqual(data['message']['to'][4]['email'], "bcc1@example.com")
        self.assertEqual(data['message']['to'][5]['email'], "bcc2@example.com")


class DjrillMandrillFeatureTests(DjrillBackendMockAPITestCase):
    """Test Djrill backend support for Mandrill-specific features"""

    def setUp(self):
        super(DjrillMandrillFeatureTests, self).setUp()
        self.message = mail.EmailMessage('Subject', 'Text Body', 'from@example.com', ['to@example.com'])

    def test_tracking(self):
        # First make sure we're not setting the API param if the track_click attr isn't there.
        # (The Mandrill account option of True for html, False for plaintext can't be communicated through
        # the API, other than by omitting the track_clicks API param to use your account default.)
        self.message.send()
        data = self.get_api_call_data()
        self.assertFalse('track_clicks' in data['message'])
        # Now re-send with the params set
        self.message.track_opens = True
        self.message.track_clicks = True
        self.message.url_strip_qs = True
        self.message.send()
        data = self.get_api_call_data()
        self.assertEqual(data['message']['track_opens'], True)
        self.assertEqual(data['message']['track_clicks'], True)
        self.assertEqual(data['message']['url_strip_qs'], True)

    def test_message_options(self):
        self.message.auto_text = True
        self.message.preserve_recipients = True
        self.message.send()
        data = self.get_api_call_data()
        self.assertEqual(data['message']['auto_text'], True)
        self.assertEqual(data['message']['preserve_recipients'], True)

    def test_merge(self):
        # Djrill expands simple python dicts into the more-verbose name/value structures the Mandrill API uses
        self.message.global_merge_vars = { 'GREETING': "Hello", 'ACCOUNT_TYPE': "Basic" }
        self.message.merge_vars = {
            "customer@example.com": { 'GREETING': "Dear Customer", 'ACCOUNT_TYPE': "Premium" },
            "guest@example.com": { 'GREETING': "Dear Guest" },
        }
        self.message.send()
        data = self.get_api_call_data()
        self.assertEqual(data['message']['global_merge_vars'],
            [ {'name': 'ACCOUNT_TYPE', 'value': "Basic"},
              {'name': "GREETING", 'value': "Hello"} ])
        self.assertEqual(data['message']['merge_vars'],
            [ { 'rcpt': "customer@example.com",
                'vars': [{ 'name': 'ACCOUNT_TYPE', 'value': "Premium" },
                         { 'name': "GREETING", 'value': "Dear Customer"}] },
              { 'rcpt': "guest@example.com",
                'vars': [{ 'name': "GREETING", 'value': "Dear Guest"}] }
            ])

    def test_tags(self):
        self.message.tags = ["receipt", "repeat-customer"]
        self.message.send()
        data = self.get_api_call_data()
        self.assertEqual(data['message']['tags'], ["receipt", "repeat-customer"])

    def test_google_analytics(self):
        self.message.google_analytics_domains = ["example.com"]
        self.message.google_analytics_campaign = "Email Receipts"
        self.message.send()
        data = self.get_api_call_data()
        self.assertEqual(data['message']['google_analytics_domains'], ["example.com"])
        self.assertEqual(data['message']['google_analytics_campaign'], "Email Receipts")

    def test_metadata(self):
        self.message.metadata = { 'batch_number': "12345", 'batch_type': "Receipts" }
        self.message.recipient_metadata = {
            # Djrill expands simple python dicts into the more-verbose name/value structures the Mandrill API uses
            "customer@example.com": { 'customer_id': "67890", 'order_id': "54321"  },
            "guest@example.com": { 'customer_id': "94107", 'order_id': "43215"  }
        }
        self.message.send()
        data = self.get_api_call_data()
        self.assertEqual(data['message']['metadata'], { 'batch_number': "12345", 'batch_type': "Receipts" })
        self.assertEqual(data['message']['recipient_metadata'],
            [ { 'rcpt': "customer@example.com",
                'values': { 'customer_id': "67890", 'order_id': "54321" } },
              { 'rcpt': "guest@example.com",
                'values': { 'customer_id': "94107", 'order_id': "43215" } }
            ])



class DjrillMessageTests(TestCase):
    def setUp(self):
        self.subject = "Djrill baby djrill."
        self.from_name = "Tarzan"
        self.from_email = "test@example"
        self.to = ["King Kong <kingkong@example.com>",
            "Cheetah <cheetah@example.com", "bubbles@example.com"]
        self.text_content = "Wonderful fallback text content."
        self.html_content = "<h1>That's a nice HTML email right there.</h1>"
        self.headers = {"Reply-To": "tarzan@example.com"}
        self.tags = ["track", "this"]

    def test_djrill_message_success(self):
        msg = DjrillMessage(self.subject, self.text_content, self.from_email,
            self.to, tags=self.tags, headers=self.headers,
            from_name=self.from_name)

        self.assertIsInstance(msg, DjrillMessage)
        self.assertEqual(msg.body, self.text_content)
        self.assertEqual(msg.recipients(), self.to)
        self.assertEqual(msg.tags, self.tags)
        self.assertEqual(msg.extra_headers, self.headers)
        self.assertEqual(msg.from_name, self.from_name)

    def test_djrill_message_html_success(self):
        msg = DjrillMessage(self.subject, self.text_content, self.from_email,
            self.to, tags=self.tags)
        msg.attach_alternative(self.html_content, "text/html")

        self.assertEqual(msg.alternatives[0][0], self.html_content)

    def test_djrill_message_tag_failure(self):
        with self.assertRaises(ValueError):
            DjrillMessage(self.subject, self.text_content, self.from_email,
                self.to, tags=["_fail"])

    def test_djrill_message_tag_skip(self):
        """
        Test that tags over 50 chars are not included in the tags list.
        """
        tags = ["works", "awesomesauce",
         "iwilltestmycodeiwilltestmycodeiwilltestmycodeiwilltestmycode"]
        msg = DjrillMessage(self.subject, self.text_content, self.from_email,
            self.to, tags=tags)

        self.assertIsInstance(msg, DjrillMessage)
        self.assertIn(tags[0], msg.tags)
        self.assertIn(tags[1], msg.tags)
        self.assertNotIn(tags[2], msg.tags)
