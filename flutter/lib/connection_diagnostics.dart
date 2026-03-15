import 'dart:io';

String describeConnectionIssue(Object error, {required String? serverUrl}) {
  final rawMessage = error.toString().trim();
  final uri = serverUrl == null ? null : Uri.tryParse(serverUrl);
  final host = uri?.host ?? '';
  final reachableTarget = serverUrl == null || serverUrl.trim().isEmpty
      ? 'the configured server'
      : serverUrl.trim();

  if (_looksLikeTransportFailure(error, rawMessage)) {
    if (_isLikelyTailscaleIpv4Host(host)) {
      return [
        'Could not reach $reachableTarget.',
        '',
        'This looks like a Tailscale address. Android can only reach it if the device itself has access to that Tailnet, and USB debugging does not proxy network access for the app.',
        '',
        'Try one of these:',
        '- Confirm the API is actually listening on that host and port.',
        '- Connect the phone itself to Tailscale.',
        '- If the API is running on this development machine, run `adb reverse tcp:18423 tcp:18423` and use `http://127.0.0.1:18423` in the app.',
        '- If the API is on another Tailscale node, proxy it through this machine or use a URL the phone can reach directly.',
        '',
        'Raw error: $rawMessage',
      ].join('\n');
    }

    return [
      'Could not reach $reachableTarget.',
      '',
      'Check that the IP, port, and network path are valid from this Android device.',
      '',
      'Raw error: $rawMessage',
    ].join('\n');
  }

  return rawMessage;
}

bool isLikelyTailscaleIpv4Host(String host) => _isLikelyTailscaleIpv4Host(host);

bool _looksLikeTransportFailure(Object error, String rawMessage) {
  if (error is SocketException || error is HttpException) {
    return true;
  }

  final normalized = rawMessage.toLowerCase();
  return normalized.contains('socketexception') ||
      normalized.contains('connection failed') ||
      normalized.contains(
        'connection closed before full header was received',
      ) ||
      normalized.contains('connection refused') ||
      normalized.contains('network is unreachable') ||
      normalized.contains('no route to host') ||
      normalized.contains('timed out') ||
      normalized.contains('failed host lookup');
}

bool _isLikelyTailscaleIpv4Host(String host) {
  final parts = host.split('.');
  if (parts.length != 4) {
    return false;
  }

  final octets = <int>[];
  for (final part in parts) {
    final value = int.tryParse(part);
    if (value == null || value < 0 || value > 255) {
      return false;
    }
    octets.add(value);
  }

  if (octets[0] != 100) {
    return false;
  }

  return octets[1] >= 64 && octets[1] <= 127;
}
