import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import 'models.dart';

typedef WebSocketChannelFactory = WebSocketChannel Function(Uri uri);

class ConduitApiClient {
  ConduitApiClient({
    required String baseUrl,
    http.Client? httpClient,
    WebSocketChannelFactory? webSocketChannelFactory,
  }) : _baseUrl = _normalizeBaseUrl(baseUrl),
       _httpClient = httpClient ?? http.Client(),
       _webSocketChannelFactory =
           webSocketChannelFactory ?? WebSocketChannel.connect;

  final String _baseUrl;
  final http.Client _httpClient;
  final WebSocketChannelFactory _webSocketChannelFactory;

  Future<HealthStatus> health() async {
    final response = await _httpClient.get(_uri('/health'));
    return HealthStatus.fromJson(_decodeJson(response));
  }

  Future<ModelSettings> getModelSettings() async {
    final response = await _httpClient.get(_uri('/settings/model'));
    return ModelSettings.fromJson(_decodeJson(response));
  }

  Future<ModelSettings> updateModel(String modelKey) async {
    final response = await _httpClient.put(
      _uri('/settings/model'),
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode({'model_key': modelKey}),
    );
    return ModelSettings.fromJson(_decodeJson(response));
  }

  Future<List<SessionSummary>> listSessions() async {
    final response = await _httpClient.get(_uri('/sessions'));
    final json = _decodeJson(response);
    return (json['sessions'] as List<dynamic>? ?? const [])
        .map((item) => SessionSummary.fromJson(item as Map<String, dynamic>))
        .toList();
  }

  Future<String> createSession() async {
    final response = await _httpClient.post(_uri('/sessions'));
    final json = _decodeJson(response, expectedStatusCode: 201);
    return json['session_id'] as String;
  }

  Future<SessionDetail> getSession(String sessionId) async {
    final response = await _httpClient.get(_uri('/sessions/$sessionId'));
    return SessionDetail.fromJson(_decodeJson(response));
  }

  Future<void> deleteSession(String sessionId) async {
    final response = await _httpClient.delete(_uri('/sessions/$sessionId'));
    if (response.statusCode != 204) {
      throw Exception(_decodeError(response));
    }
  }

  Future<ChatReply> sendMessage({
    required String sessionId,
    required String message,
    Map<String, dynamic>? context,
  }) async {
    final response = await _httpClient.post(
      _uri('/chat'),
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode({
        'session_id': sessionId,
        'message': message,
        if (context != null && context.isNotEmpty) 'context': context,
      }),
    );
    return ChatReply.fromJson(_decodeJson(response));
  }

  ConduitChatSocket createChatSocket() {
    return ConduitChatSocket._(
      _webSocketChannelFactory(_webSocketUri('/chat')),
    );
  }

  Uri _uri(String path) => Uri.parse('$_baseUrl$path');

  Uri _webSocketUri(String path) => webSocketUriForBaseUrl(_baseUrl, path);

  static String _normalizeBaseUrl(String value) {
    final trimmed = value.trim();
    if (trimmed.isEmpty) {
      throw ArgumentError('Server URL cannot be empty.');
    }
    final withScheme = trimmed.contains('://') ? trimmed : 'http://$trimmed';
    return withScheme.endsWith('/')
        ? withScheme.substring(0, withScheme.length - 1)
        : withScheme;
  }

  Map<String, dynamic> _decodeJson(
    http.Response response, {
    int expectedStatusCode = 200,
  }) {
    if (response.statusCode != expectedStatusCode) {
      throw Exception(_decodeError(response));
    }
    return Map<String, dynamic>.from(
      jsonDecode(response.body) as Map<String, dynamic>,
    );
  }

  String _decodeError(http.Response response) {
    try {
      final payload = jsonDecode(response.body) as Map<String, dynamic>;
      final detail = payload['detail'];
      if (detail is String && detail.isNotEmpty) {
        return detail;
      }
    } catch (_) {
      // Ignore JSON decode failures and fall back to the raw status.
    }
    return 'Request failed with status ${response.statusCode}.';
  }
}

Uri webSocketUriForBaseUrl(String baseUrl, [String path = '/chat']) {
  final normalizedBaseUrl = ConduitApiClient._normalizeBaseUrl(baseUrl);
  final normalizedPath = path.startsWith('/') ? path : '/$path';
  final httpUri = Uri.parse(normalizedBaseUrl + normalizedPath);
  final scheme = httpUri.scheme == 'https' ? 'wss' : 'ws';
  return httpUri.replace(scheme: scheme);
}

class ConduitChatSocket {
  ConduitChatSocket._(WebSocketChannel channel)
    : _channel = channel,
      _sendOverride = null,
      _disposeOverride = null,
      events = channel.stream
          .map((message) => _decodeSocketMessage(message))
          .asBroadcastStream();

  ConduitChatSocket.test({
    required Stream<ChatServerEvent> events,
    Future<void> Function(Map<String, dynamic> payload)? onSend,
    Future<void> Function()? onDispose,
  }) : _channel = null,
       _sendOverride = onSend,
       _disposeOverride = onDispose,
       events = events.asBroadcastStream();

  final WebSocketChannel? _channel;
  final Future<void> Function(Map<String, dynamic> payload)? _sendOverride;
  final Future<void> Function()? _disposeOverride;
  final Stream<ChatServerEvent> events;

  Future<void> send(Map<String, dynamic> payload) async {
    if (_channel != null) {
      _channel.sink.add(jsonEncode(payload));
      return;
    }
    if (_sendOverride != null) {
      await _sendOverride(payload);
    }
  }

  Future<void> dispose() async {
    if (_channel != null) {
      await _channel.sink.close();
      return;
    }
    if (_disposeOverride != null) {
      await _disposeOverride();
    }
  }
}

ChatServerEvent _decodeSocketMessage(dynamic message) {
  if (message is String) {
    return ChatServerEvent.fromJson(
      Map<String, dynamic>.from(jsonDecode(message) as Map),
    );
  }
  if (message is List<int>) {
    return ChatServerEvent.fromJson(
      Map<String, dynamic>.from(jsonDecode(utf8.decode(message)) as Map),
    );
  }
  throw FormatException(
    'Unsupported websocket payload type: ${message.runtimeType}',
  );
}
