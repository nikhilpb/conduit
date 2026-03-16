import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/widgets.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'conduit_api.dart';

/// Callback invoked when the user taps a notification.
typedef NotificationTapCallback = void Function(String sessionId);

class NotificationService with WidgetsBindingObserver {
  NotificationService({this.onNotificationTap});

  final NotificationTapCallback? onNotificationTap;

  final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  bool _initialized = false;
  bool _isForegrounded = true;
  /// The session ID the user is currently viewing.
  String? activeSessionId;
  String? _serverUrl;
  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _reconnectTimer;
  int _reconnectAttempt = 0;
  bool _disposed = false;

  Future<void> initialize() async {
    if (_initialized) return;
    _initialized = true;

    WidgetsBinding.instance.addObserver(this);

    try {
      const androidSettings =
          AndroidInitializationSettings('@mipmap/ic_launcher');
      const initSettings = InitializationSettings(android: androidSettings);

      await _plugin.initialize(
        initSettings,
        onDidReceiveNotificationResponse: _onNotificationResponse,
      );

      // Request notification permission on Android 13+.
      final androidPlugin =
          _plugin.resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>();
      if (androidPlugin != null) {
        await androidPlugin.requestNotificationsPermission();
      }
    } catch (_) {
      // Plugin unavailable (e.g. in tests or on unsupported platforms).
    }
  }

  void connect(String serverUrl) {
    if (_disposed) return;
    _serverUrl = serverUrl;
    _reconnectAttempt = 0;
    _reconnectTimer?.cancel();
    _doConnect();
  }

  void disconnect() {
    _serverUrl = null;
    _reconnectTimer?.cancel();
    _subscription?.cancel();
    _subscription = null;
    _channel?.sink.close();
    _channel = null;
  }

  void dispose() {
    _disposed = true;
    disconnect();
    WidgetsBinding.instance.removeObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _isForegrounded = state == AppLifecycleState.resumed;
  }

  // -- private ---------------------------------------------------------------

  void _doConnect() {
    if (_disposed || _serverUrl == null) return;

    _subscription?.cancel();
    _channel?.sink.close();

    final uri = webSocketUriForBaseUrl(_serverUrl!, '/notifications');
    _channel = WebSocketChannel.connect(uri);
    _reconnectAttempt = 0;

    _subscription = _channel!.stream.listen(
      _onMessage,
      onError: (_) => _scheduleReconnect(),
      onDone: _scheduleReconnect,
      cancelOnError: false,
    );
  }

  void _scheduleReconnect() {
    if (_disposed || _serverUrl == null) return;
    _subscription?.cancel();
    _subscription = null;
    _channel = null;

    final delay = math.min(30, math.pow(2, _reconnectAttempt).toInt());
    _reconnectAttempt++;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(Duration(seconds: delay), _doConnect);
  }

  void _onMessage(dynamic raw) {
    if (_disposed) return;

    final Map<String, dynamic> data;
    try {
      data = Map<String, dynamic>.from(
        jsonDecode(raw is String ? raw : utf8.decode(raw as List<int>)) as Map,
      );
    } catch (_) {
      return;
    }

    final sessionId = data['session_id'] as String?;
    if (sessionId == null) return;

    // Suppress if foregrounded or viewing this session.
    if (_isForegrounded) return;
    if (sessionId == activeSessionId) return;

    final sessionKind = data['session_kind'] as String? ?? 'interactive';
    final rawTitle = data['session_title'] as String? ?? 'New message';
    final title =
        sessionKind == 'scheduled' ? 'Scheduled: $rawTitle' : rawTitle;
    final body = data['preview'] as String? ?? '';

    _showNotification(
      id: sessionId.hashCode,
      title: title,
      body: body,
      payload: sessionId,
    );
  }

  Future<void> _showNotification({
    required int id,
    required String title,
    required String body,
    String? payload,
  }) async {
    try {
      const androidDetails = AndroidNotificationDetails(
        'conduit_messages',
        'Messages',
        channelDescription: 'Conduit assistant messages',
        importance: Importance.high,
        priority: Priority.high,
      );
      const details = NotificationDetails(android: androidDetails);
      await _plugin.show(id, title, body, details, payload: payload);
    } catch (_) {
      // Plugin unavailable.
    }
  }

  void _onNotificationResponse(NotificationResponse response) {
    final payload = response.payload;
    if (payload != null && payload.isNotEmpty && onNotificationTap != null) {
      onNotificationTap!(payload);
    }
  }
}
