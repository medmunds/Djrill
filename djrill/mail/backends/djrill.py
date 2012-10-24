from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import sanitize_address
from django.utils import simplejson as json

from email.utils import parseaddr
import requests

class DjrillBackendHTTPError(Exception):
    """An exception that will turn into an HTTP error response."""
    def __init__(self, status_code, log_message=None):
        super(DjrillBackendHTTPError, self).__init__()
        self.status_code = status_code
        self.log_message = log_message

    def __str__(self):
        message = "DjrillBackendHTTP %d" % self.status_code
        if self.log_message:
            return message + " " + self.log_message
        else:
            return message


class DjrillBackend(BaseEmailBackend):
    """
    Mandrill API Email Backend
    """

    def __init__(self, fail_silently=False, **kwargs):
        """
        Set the API key, API url and set the action url.
        """
        super(DjrillBackend, self).__init__(**kwargs)
        self.api_key = getattr(settings, "MANDRILL_API_KEY", None)
        self.api_url = getattr(settings, "MANDRILL_API_URL", None)
        self.connection = None

        if not self.api_key:
            raise ImproperlyConfigured("You have not set your mandrill api key "
                "in the settings.py file.")
        if not self.api_url:
            raise ImproperlyConfigured("You have not added the Mandrill api "
                "url to your settings.py")

        self.api_action = self.api_url + "/messages/send.json"
        self.api_verify = self.api_url + "/users/verify-sender.json"

    def open(self, sender):
        """
        """
        self.connection = True
        # self.connection = None
        # 
        # valid_sender = requests.post(
        #     self.api_verify, data={"key": self.api_key, "email": sender})
        # 
        # if valid_sender.status_code == 200:
        #     data = json.loads(valid_sender.content)
        #     if data["is_enabled"]:
        #         self.connection = True
        #         return True
        # else:
        #     if not self.fail_silently:
        #         raise

    def send_messages(self, email_messages):
        if not email_messages:
            return

        num_sent = 0
        for message in email_messages:
            self.open(message.from_email)
            if not self.connection:
                return

            sent = self._send(message)

            if sent:
                num_sent += 1

        return num_sent

    def _send(self, message):
        if not message.recipients():
            return False

        self.sender = sanitize_address(message.from_email, message.encoding)
        recipients_list = [sanitize_address(addr, message.encoding)
            for addr in message.recipients()]
        self.recipients = [{"email": e, "name": n} for n,e in [
            parseaddr(r) for r in recipients_list]]

        self.msg_dict = self._build_standard_message_dict(message)

        if getattr(message, "alternative_subtype", None):
            if message.alternative_subtype == "mandrill":
                if message.alternatives:
                    self._add_alternatives(message)

        djrill_it = requests.post(self.api_action, data=json.dumps({
            "key": self.api_key,
            "message": self.msg_dict
        }))

        if djrill_it.status_code != 200:
            if not self.fail_silently:
                raise DjrillBackendHTTPError(status_code=djrill_it.status_code, log_message="Failed to send a message to %s, from %s" % (self.recipients, self.sender))
            return False
        return True

    def _build_standard_message_dict(self, message):
        """
        Build standard message dict.

        Builds the standard dict that Django's send_mail and send_mass_mail
        use by default. Standard text email messages sent through Django will
        still work through Mandrill.
        """
        name, email = parseaddr(self.sender)
        msg_dict = {
            "text": message.body,
            "subject": message.subject,
            "from_email": email,
            "from_name": name,
            "to": self.recipients
        }

        if message.extra_headers:
            accepted_headers = {}
            for k in message.extra_headers.keys():
                if k.startswith("X-") or k == "Reply-To":
                    accepted_headers.update(
                        {"%s" % k: message.extra_headers[k]})
            msg_dict.update({"headers": accepted_headers})

        # Mandrill attributes that can be copied directly
        mandrill_attrs = [
            'from_name', # deprecated Djrill legacy - overrides display name parsed from from_email above
            'track_opens', 'track_clicks', 'auto_text', 'url_strip_qs', 'preserve_recipients',
            'tags', 'google_analytics_domains', 'google_analytics_campaign',
            'metadata']
        for attr in mandrill_attrs:
            if hasattr(message, attr):
                msg_dict[attr] = getattr(message, attr)

        # Allow simple python dicts in place of Mandrill [{name:name, value:value},...] arrays...
        if hasattr(message, 'global_merge_vars'):
            msg_dict['global_merge_vars'] = self._expand_merge_vars(message.global_merge_vars)
        if hasattr(message, 'merge_vars'):
            # For testing reproducibility, we sort the recipients
            msg_dict['merge_vars'] = [
                { 'rcpt': rcpt, 'vars': self._expand_merge_vars(message.merge_vars[rcpt]) }
                for rcpt in sorted(message.merge_vars.keys())
            ]
        if hasattr(message, 'recipient_metadata'):
            # For testing reproducibility, we sort the recipients
            msg_dict['recipient_metadata'] = [
                { 'rcpt': rcpt, 'values': message.recipient_metadata[rcpt] }
                for rcpt in sorted(message.recipient_metadata.keys())
            ]

        return msg_dict

    def _expand_merge_vars(self, vars):
        """Convert a dict of { name: value, ... } to [ {'name': name, 'value': value }, ... ]"""
        # For testing reproducibility, we sort the keys
        return [ { 'name': name, 'value': vars[name] } for name in sorted(vars.keys()) ]

    def _add_alternatives(self, message):
        """
        There can be only one! ... alternative attachment.

        Since mandrill does not accept image attachments or anything other
        than HTML, the assumption is the only thing you are attaching is
        the HTML output for your email.
        """
        if len(message.alternatives) > 1:
            raise ValueError(
                "Mandrill only accepts plain text and html emails. Please "
                "check the alternatives you have attached to your message.")

        self.msg_dict.update({
            "html": message.alternatives[0][0]
        })
