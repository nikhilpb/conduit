import 'package:flutter_test/flutter_test.dart';
import 'package:think_client/conduit_api.dart';

void main() {
  test('webSocketUriForBaseUrl converts http urls to ws urls', () {
    expect(
      webSocketUriForBaseUrl('http://10.0.2.2:18423').toString(),
      'ws://10.0.2.2:18423/chat',
    );
  });

  test('webSocketUriForBaseUrl converts https urls to wss urls', () {
    expect(
      webSocketUriForBaseUrl('https://example.com').toString(),
      'wss://example.com/chat',
    );
  });
}
