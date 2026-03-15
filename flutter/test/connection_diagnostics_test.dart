import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:think_client/connection_diagnostics.dart';

void main() {
  test('tailscale-style transport failures explain Android routing issue', () {
    final message = describeConnectionIssue(
      const SocketException('Connection failed'),
      serverUrl: 'http://100.118.83.127:18423',
    );

    expect(message, contains('Could not reach http://100.118.83.127:18423.'));
    expect(message, contains('USB debugging does not proxy network access'));
    expect(message, contains('adb reverse tcp:18423 tcp:18423'));
  });

  test('non-tailscale transport failures stay generic', () {
    final message = describeConnectionIssue(
      const SocketException('Connection failed'),
      serverUrl: 'http://192.168.1.20:18423',
    );

    expect(message, contains('Could not reach http://192.168.1.20:18423.'));
    expect(
      message,
      isNot(contains('USB debugging does not proxy network access')),
    );
  });

  test('tailscale host detection only matches 100.64.0.0/10', () {
    expect(isLikelyTailscaleIpv4Host('100.118.83.127'), isTrue);
    expect(isLikelyTailscaleIpv4Host('100.63.83.127'), isFalse);
    expect(isLikelyTailscaleIpv4Host('100.128.83.127'), isFalse);
    expect(isLikelyTailscaleIpv4Host('example.com'), isFalse);
  });
}
