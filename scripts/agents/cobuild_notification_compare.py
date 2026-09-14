"""DSS-macro-only SMTP capture test. Nothing is delivered outside localhost."""
import email
import secrets
import socketserver
import threading


def fixtures(run):
    received = []
    marker = 'ADTK comparison ' + secrets.token_hex(8)
    channel_id = 'atk-ab-' + secrets.token_hex(4)
    channel = None

    class SMTP(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(30)
            self.wfile.write(b'220 localhost ADTK test sink\r\n')
            data = None
            while True:
                line = self.rfile.readline(8192)
                if not line:
                    return
                if data is not None:
                    if line == b'.\r\n':
                        received.append(bytes(data))
                        data = None
                        self.wfile.write(b'250 captured locally\r\n')
                    else:
                        data.extend(line[1:] if line.startswith(b'..') else line)
                        if len(data) > 65536:
                            return
                elif line.upper().startswith((b'EHLO', b'HELO')):
                    self.wfile.write(b'250 localhost\r\n')
                elif line.upper().startswith(b'DATA'):
                    data = bytearray()
                    self.wfile.write(b'354 finish with dot\r\n')
                elif line.upper().startswith(b'QUIT'):
                    self.wfile.write(b'221 goodbye\r\n')
                    return
                else:
                    self.wfile.write(b'250 OK\r\n')

    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), SMTP)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        channel = run.dss.create_messaging_channel('smtp', channel_id=channel_id,
                   channel_configuration={'host': '127.0.0.1', 'port': server.server_address[1],
                       'useSSL': False, 'useTLS': False, 'useCurrentUserAsSender': False,
                       'sender': 'adtk@test.invalid', 'authorizedDomain': 'test.invalid'},
                   permissions=[{'user': 'admin', 'canUse': True}])

        def reset():
            received.clear()

        def verify(_):
            assert len(received) == 1, 'Expected exactly one local delivery'
            message = email.message_from_bytes(received[0])
            assert message['Subject'] == marker
            assert 'capture@test.invalid' in message['To']
            texts = [part.get_payload(decode=True) or b'' for part in message.walk() if not part.is_multipart()]
            assert any(marker.encode() in part for part in texts)
            return {'local_deliveries': 1, 'subject_matches': True, 'body_matches': True,
                    'external_delivery': False}

        with run.gates(['notification-send']):
            run.action('notification-send', {'channelId': channel_id, 'recipients': ['capture@test.invalid'],
                                              'subject': marker, 'message': marker}, reset, verify,
                       'Temporary SMTP channel → localhost capture sink; no message to a person')
    finally:
        errors = []
        if channel:
            try:
                channel.delete()
            except Exception as exc:
                errors.append('channel: ' + type(exc).__name__)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        run.save({'capability': '_notification_fixture', 'status': 'cleanup_failed' if errors else 'deleted',
                  'errors': errors})
