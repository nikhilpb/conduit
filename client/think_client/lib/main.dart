import 'dart:convert';
import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:google_fonts/google_fonts.dart';

import 'conduit_api.dart';
import 'models.dart';
import 'settings_store.dart';

const _defaultServerUrl = String.fromEnvironment('CONDUIT_SERVER_URL');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final settingsStore = SettingsStore();
  final storedServerUrl = await settingsStore.loadServerUrl();
  final initialServerUrl = _resolveInitialServerUrl(
    storedServerUrl: storedServerUrl,
  );
  runApp(
    ConduitApp(
      initialServerUrl: initialServerUrl,
      settingsStore: settingsStore,
    ),
  );
}

class ConduitApp extends StatefulWidget {
  const ConduitApp({
    super.key,
    required this.initialServerUrl,
    required this.settingsStore,
  });

  final String? initialServerUrl;
  final SettingsStore settingsStore;

  @override
  State<ConduitApp> createState() => _ConduitAppState();
}

class _ConduitAppState extends State<ConduitApp> {
  late String? _serverUrl = widget.initialServerUrl;

  @override
  Widget build(BuildContext context) {
    final baseTextTheme = GoogleFonts.ibmPlexSansTextTheme();
    return MaterialApp(
      title: 'Conduit',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFFB85C38),
          primary: const Color(0xFFB85C38),
          secondary: const Color(0xFF184E77),
          surface: const Color(0xFFFFFBF5),
        ),
        scaffoldBackgroundColor: const Color(0xFFF3EEE6),
        textTheme: GoogleFonts.spaceGroteskTextTheme(baseTextTheme).copyWith(
          bodyLarge: GoogleFonts.ibmPlexSans(
            textStyle: baseTextTheme.bodyLarge,
          ),
          bodyMedium: GoogleFonts.ibmPlexSans(
            textStyle: baseTextTheme.bodyMedium,
          ),
          bodySmall: GoogleFonts.ibmPlexSans(
            textStyle: baseTextTheme.bodySmall,
          ),
        ),
        appBarTheme: const AppBarTheme(
          backgroundColor: Color(0xFFF3EEE6),
          foregroundColor: Color(0xFF16202A),
          surfaceTintColor: Colors.transparent,
          elevation: 0,
        ),
        cardTheme: CardThemeData(
          color: const Color(0xFFFFFBF5),
          elevation: 0,
          margin: EdgeInsets.zero,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(28),
            side: const BorderSide(color: Color(0xFFE6DDCF)),
          ),
        ),
        snackBarTheme: const SnackBarThemeData(
          behavior: SnackBarBehavior.floating,
        ),
      ),
      home: SessionListScreen(
        serverUrl: _serverUrl,
        settingsStore: widget.settingsStore,
        onServerUrlChanged: (serverUrl) {
          setState(() {
            _serverUrl = serverUrl;
          });
        },
      ),
    );
  }
}

class SessionListScreen extends StatefulWidget {
  const SessionListScreen({
    super.key,
    required this.serverUrl,
    required this.settingsStore,
    required this.onServerUrlChanged,
  });

  final String? serverUrl;
  final SettingsStore settingsStore;
  final ValueChanged<String?> onServerUrlChanged;

  @override
  State<SessionListScreen> createState() => _SessionListScreenState();
}

class _SessionListScreenState extends State<SessionListScreen> {
  List<SessionSummary> _sessions = const [];
  HealthStatus? _health;
  bool _loading = true;
  String? _error;

  ConduitApiClient? get _client {
    final serverUrl = widget.serverUrl;
    if (serverUrl == null || serverUrl.trim().isEmpty) {
      return null;
    }
    return ConduitApiClient(baseUrl: serverUrl);
  }

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void didUpdateWidget(covariant SessionListScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.serverUrl != widget.serverUrl) {
      _refresh();
    }
  }

  Future<void> _refresh() async {
    final client = _client;
    if (client == null) {
      setState(() {
        _loading = false;
        _error = null;
        _health = null;
        _sessions = const [];
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final results = await Future.wait<dynamic>([
        client.health(),
        client.listSessions(),
      ]);
      if (!mounted) {
        return;
      }
      setState(() {
        _health = results[0] as HealthStatus;
        _sessions = results[1] as List<SessionSummary>;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = '$error';
        _loading = false;
      });
    }
  }

  Future<void> _openSettings() async {
    final updatedServerUrl = await Navigator.of(context).push<String>(
      MaterialPageRoute<String>(
        builder: (context) => SettingsScreen(
          currentServerUrl: widget.serverUrl,
          settingsStore: widget.settingsStore,
        ),
      ),
    );
    if (updatedServerUrl != null && updatedServerUrl != widget.serverUrl) {
      widget.onServerUrlChanged(updatedServerUrl);
      return;
    }
    await _refresh();
  }

  Future<void> _createSession() async {
    final client = _client;
    if (client == null) {
      await _openSettings();
      return;
    }
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (context) =>
            ChatScreen(client: client, settingsStore: widget.settingsStore),
      ),
    );
    await _refresh();
  }

  Future<void> _openSession(SessionSummary session) async {
    final client = _client;
    if (client == null) {
      return;
    }
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (context) => ChatScreen(
          client: client,
          settingsStore: widget.settingsStore,
          sessionId: session.sessionId,
          initialTitle: session.title,
        ),
      ),
    );
    await _refresh();
  }

  Future<bool> _deleteSession(SessionSummary session) async {
    final client = _client;
    if (client == null) {
      return false;
    }

    try {
      await client.deleteSession(session.sessionId);
      if (!mounted) {
        return false;
      }
      setState(() {
        _sessions = _sessions
            .where((item) => item.sessionId != session.sessionId)
            .toList();
      });
      return true;
    } catch (error) {
      if (!mounted) {
        return false;
      }
      _showSnackBar('Could not delete session: $error');
      return false;
    }
  }

  void _showSnackBar(String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final serverUrl = widget.serverUrl;
    final sessions = _sessions;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Conduit'),
        actions: [
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: StatusBadge(
              connected: _health?.ok == true,
              label: _health?.ok == true ? 'Connected' : 'Offline',
            ),
          ),
          IconButton(
            tooltip: 'Settings',
            onPressed: _openSettings,
            icon: const Icon(Icons.tune),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _createSession,
        backgroundColor: const Color(0xFFB85C38),
        foregroundColor: Colors.white,
        icon: const Icon(Icons.add_comment_outlined),
        label: const Text('New session'),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 120),
          children: [
            if (serverUrl == null || serverUrl.trim().isEmpty)
              EmptyServerCard(onConfigure: _openSettings)
            else ...[
              if (_loading)
                const Center(
                  child: Padding(
                    padding: EdgeInsets.symmetric(vertical: 48),
                    child: CircularProgressIndicator(),
                  ),
                )
              else if (_error != null)
                ErrorCard(message: _error!, onRetry: _refresh)
              else if (sessions.isEmpty)
                const EmptySessionsCard()
              else
                ...sessions.map(
                  (session) => Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Dismissible(
                      key: ValueKey(session.sessionId),
                      direction: DismissDirection.endToStart,
                      confirmDismiss: (_) => _deleteSession(session),
                      background: Container(
                        alignment: Alignment.centerRight,
                        padding: const EdgeInsets.symmetric(horizontal: 24),
                        decoration: BoxDecoration(
                          color: const Color(0xFFB42318),
                          borderRadius: BorderRadius.circular(28),
                        ),
                        child: const Icon(
                          Icons.delete_outline,
                          color: Colors.white,
                        ),
                      ),
                      child: SessionCard(
                        session: session,
                        onTap: () => _openSession(session),
                      ),
                    ),
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({
    super.key,
    required this.client,
    required this.settingsStore,
    this.sessionId,
    this.initialTitle,
  });

  final ConduitApiClient client;
  final SettingsStore settingsStore;
  final String? sessionId;
  final String? initialTitle;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _composer = TextEditingController();
  final ScrollController _scrollController = ScrollController();

  List<TranscriptMessage> _messages = const [];
  bool _loading = true;
  String? _error;
  ConduitChatSocket? _chatSocket;
  StreamSubscription<ChatServerEvent>? _chatSubscription;
  Timer? _reconnectTimer;
  int _reconnectAttempt = 0;
  _ChatConnectionState _connectionState = _ChatConnectionState.connecting;
  String? _connectionError;
  Map<String, _PendingTurn> _pendingTurns = const {};
  Map<String, String> _clientMessageIdByTurnId = const {};
  _PendingApprovalRequest? _pendingApproval;
  bool _submittingApproval = false;
  bool _disposed = false;
  String? _activeModelLabel;
  late String? _sessionId = widget.sessionId;
  late String _sessionTitle = widget.initialTitle ?? 'New conversation';

  bool get _sending => _pendingTurns.isNotEmpty;

  @override
  void initState() {
    super.initState();
    _loadMessages();
    _loadActiveModel();
    _connectChat();
  }

  @override
  void dispose() {
    _disposed = true;
    _reconnectTimer?.cancel();
    unawaited(_chatSubscription?.cancel() ?? Future<void>.value());
    unawaited(_chatSocket?.dispose() ?? Future<void>.value());
    _composer.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _loadMessages() async {
    final sessionId = _sessionId;
    if (sessionId == null) {
      setState(() {
        _messages = const [];
        _loading = false;
        _error = null;
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final detail = await widget.client.getSession(sessionId);
      if (!mounted) {
        return;
      }
      setState(() {
        _messages = detail.messages;
        _loading = false;
        _sessionTitle = _deriveSessionTitle(detail.messages) ?? _sessionTitle;
      });
      _scrollToBottom();
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = '$error';
        _loading = false;
      });
    }
  }

  Future<void> _loadActiveModel() async {
    try {
      final health = await widget.client.health();
      if (!mounted) {
        return;
      }
      setState(() {
        _activeModelLabel = health.modelLabel.isNotEmpty
            ? health.modelLabel
            : health.model;
      });
    } catch (_) {
      if (!mounted) {
        return;
      }
      setState(() {
        _activeModelLabel = null;
      });
    }
  }

  Future<void> _sendMessage() async {
    final text = _composer.text.trim();
    if (text.isEmpty || _sending) {
      return;
    }

    final turnContext = await _buildTurnContext();
    final clientMessageId = _makeClientMessageId();
    final sessionId = _sessionId ?? _makeDraftSessionId();
    final now = DateTime.now().millisecondsSinceEpoch / 1000;
    final assistantMessageId = 'local-assistant-$clientMessageId';
    final optimisticMessage = TranscriptMessage(
      messageId: 'local-user-$clientMessageId',
      role: 'user',
      text: text,
      createdAt: now,
      thinkingTrace: '',
      toolCalls: const [],
    );
    final optimisticAssistantMessage = TranscriptMessage(
      messageId: assistantMessageId,
      role: 'assistant',
      text: '',
      createdAt: now,
      thinkingTrace: '',
      toolCalls: const [],
    );

    setState(() {
      _messages = [..._messages, optimisticMessage, optimisticAssistantMessage];
      _error = null;
      _connectionError = null;
      _sessionId = sessionId;
      if (_sessionTitle == 'New conversation') {
        _sessionTitle = _deriveTitleFromText(text);
      }
      _pendingTurns = {
        ..._pendingTurns,
        clientMessageId: _PendingTurn(
          clientMessageId: clientMessageId,
          text: text,
          context: turnContext,
          userMessageId: optimisticMessage.messageId,
          assistantMessageId: assistantMessageId,
        ),
      };
      _composer.clear();
    });
    _scrollToBottom();

    await _sendPendingTurn(clientMessageId);
  }

  void _showUnavailable(String featureName) {
    _showSnackBar('$featureName is not wired yet.');
  }

  void _showSnackBar(String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) {
        return;
      }
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOutCubic,
      );
    });
  }

  Future<void> _connectChat({bool isReconnect = false}) async {
    if (_disposed) {
      return;
    }

    _reconnectTimer?.cancel();
    final socket = widget.client.createChatSocket();
    final subscription = socket.events.listen(
      (event) => _handleChatEvent(socket, event),
      onError: (Object error, StackTrace stackTrace) {
        _handleSocketClosed(socket, error: '$error');
      },
      onDone: () {
        _handleSocketClosed(socket);
      },
      cancelOnError: false,
    );

    if (!mounted || _disposed) {
      await subscription.cancel();
      await socket.dispose();
      return;
    }

    setState(() {
      _chatSocket = socket;
      _chatSubscription = subscription;
      _connectionState = _ChatConnectionState.connected;
      _connectionError = null;
    });
    _reconnectAttempt = 0;
    await _replayPendingTurns();
  }

  void _handleSocketClosed(ConduitChatSocket socket, {String? error}) {
    if (_disposed || !mounted || !identical(socket, _chatSocket)) {
      return;
    }

    unawaited(_chatSubscription?.cancel() ?? Future<void>.value());
    unawaited(socket.dispose());
    _chatSubscription = null;
    _chatSocket = null;
    _scheduleReconnect(error: error);
  }

  void _scheduleReconnect({String? error}) {
    if (_disposed || !mounted) {
      return;
    }
    if (_reconnectTimer?.isActive == true) {
      return;
    }

    _resetPendingTurnsForReplay();
    final seconds = math.min(30, 1 << _reconnectAttempt.clamp(0, 4));
    _reconnectAttempt += 1;
    setState(() {
      _connectionState = _ChatConnectionState.reconnecting;
      _connectionError = error ?? 'Chat connection lost. Retrying.';
    });
    _reconnectTimer = Timer(Duration(seconds: seconds), () {
      unawaited(_connectChat(isReconnect: true));
    });
  }

  Future<void> _replayPendingTurns() async {
    final pendingIds = _pendingTurns.keys.toList(growable: false);
    for (final clientMessageId in pendingIds) {
      await _sendPendingTurn(clientMessageId);
    }
  }

  Future<void> _sendPendingTurn(String clientMessageId) async {
    final pendingTurn = _pendingTurns[clientMessageId];
    final socket = _chatSocket;
    if (pendingTurn == null) {
      return;
    }
    if (socket == null) {
      if (_connectionState == _ChatConnectionState.offline) {
        unawaited(_connectChat(isReconnect: true));
      }
      return;
    }

    try {
      await socket.send({
        'type': 'text',
        if (_sessionId != null) 'session_id': _sessionId,
        'message_id': pendingTurn.clientMessageId,
        'content': pendingTurn.text,
        'context': pendingTurn.context.toJson(),
      });
    } catch (error) {
      _handleSocketClosed(socket, error: '$error');
    }
  }

  Future<_TurnContextPayload> _buildTurnContext() async {
    final savedSettings = await widget.settingsStore.loadUserContextSettings();
    return _TurnContextPayload(
      currentTime: _formatCurrentTimeForContext(DateTime.now()),
      location: savedSettings.location,
      personalInstructions: savedSettings.personalInstructions,
    );
  }

  void _handleChatEvent(ConduitChatSocket socket, ChatServerEvent event) {
    if (_disposed || !mounted || !identical(socket, _chatSocket)) {
      return;
    }

    switch (event.type) {
      case 'ack':
        _handleAck(event);
        return;
      case 'tool_call':
        _handleToolCall(event);
        return;
      case 'tool_result':
        _handleToolResult(event);
        return;
      case 'thought':
        _handleThought(event);
        return;
      case 'token':
        _handleToken(event);
        return;
      case 'done':
        _handleDone(event);
        return;
      case 'approval_required':
        _handleApprovalRequired(event);
        return;
      case 'error':
        _handleServerError(event);
        return;
      default:
        return;
    }
  }

  void _handleAck(ChatServerEvent event) {
    final messageId = event.messageId;
    final turnId = event.turnId;
    if (messageId == null || turnId == null) {
      return;
    }
    final pendingTurn = _pendingTurns[messageId];
    if (pendingTurn == null) {
      return;
    }

    setState(() {
      _sessionId = event.sessionId ?? _sessionId;
      _pendingTurns = {
        ..._pendingTurns,
        messageId: pendingTurn.copyWith(turnId: turnId),
      };
      _clientMessageIdByTurnId = {
        ..._clientMessageIdByTurnId,
        turnId: messageId,
      };
      _connectionState = _ChatConnectionState.connected;
      _connectionError = null;
    });
  }

  void _handleToolCall(ChatServerEvent event) {
    final clientMessageId = _clientMessageIdForTurn(event.turnId);
    final toolName = event.tool;
    if (clientMessageId == null || toolName == null) {
      return;
    }
    final pendingTurn = _pendingTurns[clientMessageId];
    if (pendingTurn == null) {
      return;
    }
    final toolCallId = event.toolCallId ?? toolName;
    if (pendingTurn.seenToolCallIds.contains(toolCallId)) {
      return;
    }

    final assistantMessageId = _assistantMessageIdForPending(
      pendingTurn,
      serverMessageId: event.messageId,
    );
    final updatedToolCalls = _mergeToolCalls(
      _toolCallsForMessage(assistantMessageId),
      ToolCall(
        toolCallId: toolCallId,
        name: toolName,
        args: event.args,
        status: event.status ?? 'pending',
        error: event.error,
        response: event.response,
      ),
    );

    _updateAssistantMessage(
      clientMessageId: clientMessageId,
      assistantMessageId: assistantMessageId,
      toolCalls: updatedToolCalls,
      seenToolCallIds: [...pendingTurn.seenToolCallIds, toolCallId],
    );
  }

  void _handleToolResult(ChatServerEvent event) {
    final clientMessageId = _clientMessageIdForTurn(event.turnId);
    final toolName = event.tool;
    if (clientMessageId == null || toolName == null) {
      return;
    }
    final pendingTurn = _pendingTurns[clientMessageId];
    if (pendingTurn == null) {
      return;
    }

    final assistantMessageId = _assistantMessageIdForPending(
      pendingTurn,
      serverMessageId: event.messageId,
    );
    final updatedToolCalls = _mergeToolCalls(
      _toolCallsForMessage(assistantMessageId),
      ToolCall(
        toolCallId: event.toolCallId,
        name: toolName,
        args: const {},
        status: event.status ?? 'completed',
        error: event.error,
        response: event.response,
      ),
    );

    _updateAssistantMessage(
      clientMessageId: clientMessageId,
      assistantMessageId: assistantMessageId,
      toolCalls: updatedToolCalls,
    );
  }

  void _handleToken(ChatServerEvent event) {
    final clientMessageId = _clientMessageIdForTurn(event.turnId);
    final content = event.content;
    if (clientMessageId == null || content == null || content.isEmpty) {
      return;
    }
    final pendingTurn = _pendingTurns[clientMessageId];
    if (pendingTurn == null) {
      return;
    }

    final assistantMessageId = _assistantMessageIdForPending(
      pendingTurn,
      serverMessageId: event.messageId,
    );
    final existing = _messageById(assistantMessageId);
    _updateAssistantMessage(
      clientMessageId: clientMessageId,
      assistantMessageId: assistantMessageId,
      text: (existing?.text ?? '') + content,
    );
    _scrollToBottom();
  }

  void _handleThought(ChatServerEvent event) {
    final clientMessageId = _clientMessageIdForTurn(event.turnId);
    final content = event.content;
    if (clientMessageId == null || content == null || content.isEmpty) {
      return;
    }
    final pendingTurn = _pendingTurns[clientMessageId];
    if (pendingTurn == null) {
      return;
    }

    final assistantMessageId = _assistantMessageIdForPending(
      pendingTurn,
      serverMessageId: event.messageId,
    );
    final existing = _messageById(assistantMessageId);
    final separator = (existing?.thinkingTrace.isNotEmpty ?? false)
        ? '\n\n'
        : '';
    _updateAssistantMessage(
      clientMessageId: clientMessageId,
      assistantMessageId: assistantMessageId,
      thinkingTrace: '${existing?.thinkingTrace ?? ''}$separator$content',
    );
    _scrollToBottom();
  }

  void _handleDone(ChatServerEvent event) {
    final clientMessageId = _clientMessageIdForTurn(event.turnId);
    if (clientMessageId == null) {
      return;
    }
    final pendingTurn = _pendingTurns[clientMessageId];
    if (pendingTurn == null) {
      return;
    }

    setState(() {
      final nextPendingTurns = {..._pendingTurns};
      nextPendingTurns.remove(clientMessageId);
      _pendingTurns = nextPendingTurns;

      final nextTurnMap = {..._clientMessageIdByTurnId};
      if (pendingTurn.turnId != null) {
        nextTurnMap.remove(pendingTurn.turnId);
      }
      _clientMessageIdByTurnId = nextTurnMap;
      _connectionState = _ChatConnectionState.connected;
      _connectionError = null;
    });
  }

  void _handleApprovalRequired(ChatServerEvent event) {
    final approvalId = event.approvalId;
    final turnId = event.turnId;
    if (approvalId == null || turnId == null) {
      return;
    }

    final nextApproval = _PendingApprovalRequest(
      approvalId: approvalId,
      turnId: turnId,
      tool: event.tool ?? 'tool',
      args: event.args,
      summary: event.summary ?? 'Approve this tool call?',
    );
    if (_pendingApproval?.approvalId == nextApproval.approvalId) {
      return;
    }

    setState(() {
      _pendingApproval = nextApproval;
      _submittingApproval = false;
    });
    _scrollToBottom();
  }

  void _handleServerError(ChatServerEvent event) {
    final turnId = event.turnId;
    final clientMessageId = _clientMessageIdForTurn(turnId);

    if (clientMessageId != null) {
      final pendingTurn = _pendingTurns[clientMessageId];
      setState(() {
        final nextPendingTurns = {..._pendingTurns};
        nextPendingTurns.remove(clientMessageId);
        _pendingTurns = nextPendingTurns;

        final nextTurnMap = {..._clientMessageIdByTurnId};
        if (pendingTurn?.turnId != null) {
          nextTurnMap.remove(pendingTurn!.turnId);
        }
        _clientMessageIdByTurnId = nextTurnMap;
        if (pendingTurn?.assistantMessageId != null) {
          _messages = _messages
              .where(
                (message) =>
                    message.messageId != pendingTurn!.assistantMessageId,
              )
              .toList();
        }
      });
    }

    final message = event.message ?? 'The server reported an error.';
    setState(() {
      if (_pendingApproval?.turnId == turnId) {
        _pendingApproval = null;
        _submittingApproval = false;
      }
      _connectionError = message;
    });
    _showSnackBar(message);
  }

  String? _clientMessageIdForTurn(String? turnId) {
    if (turnId == null) {
      return null;
    }
    return _clientMessageIdByTurnId[turnId];
  }

  String _assistantMessageIdForPending(
    _PendingTurn pendingTurn, {
    String? serverMessageId,
  }) {
    return serverMessageId ??
        pendingTurn.assistantMessageId ??
        'local-assistant-${pendingTurn.clientMessageId}';
  }

  TranscriptMessage? _messageById(String messageId) {
    for (final message in _messages) {
      if (message.messageId == messageId) {
        return message;
      }
    }
    return null;
  }

  List<ToolCall> _toolCallsForMessage(String messageId) {
    final message = _messageById(messageId);
    return message?.toolCalls ?? const [];
  }

  List<ToolCall> _mergeToolCalls(
    List<ToolCall> existingToolCalls,
    ToolCall nextToolCall,
  ) {
    final existingIndex = _toolCallIndex(existingToolCalls, nextToolCall);
    if (existingIndex < 0) {
      return [...existingToolCalls, nextToolCall];
    }

    final currentToolCall = existingToolCalls[existingIndex];
    final mergedToolCall = ToolCall(
      toolCallId: nextToolCall.toolCallId ?? currentToolCall.toolCallId,
      name: nextToolCall.name,
      args: nextToolCall.args.isNotEmpty
          ? nextToolCall.args
          : currentToolCall.args,
      status: nextToolCall.status,
      error: nextToolCall.status == 'completed'
          ? null
          : (nextToolCall.error ?? currentToolCall.error),
      response: nextToolCall.response ?? currentToolCall.response,
    );

    final nextToolCalls = [...existingToolCalls];
    nextToolCalls[existingIndex] = mergedToolCall;
    return nextToolCalls;
  }

  int _toolCallIndex(List<ToolCall> toolCalls, ToolCall nextToolCall) {
    final toolCallId = nextToolCall.toolCallId;
    if (toolCallId != null && toolCallId.isNotEmpty) {
      return toolCalls.indexWhere(
        (toolCall) => toolCall.toolCallId == toolCallId,
      );
    }
    return toolCalls.indexWhere(
      (toolCall) => toolCall.name == nextToolCall.name,
    );
  }

  bool _isAssistantMessagePending(TranscriptMessage message) {
    if (message.isUser) {
      return false;
    }
    for (final turn in _pendingTurns.values) {
      if (turn.assistantMessageId != message.messageId) {
        continue;
      }
      if (_pendingApproval != null && _pendingApproval!.turnId == turn.turnId) {
        return false;
      }
      return true;
    }
    return false;
  }

  void _updateAssistantMessage({
    required String clientMessageId,
    required String assistantMessageId,
    String? text,
    String? thinkingTrace,
    List<ToolCall>? toolCalls,
    List<String>? seenToolCallIds,
  }) {
    final pendingTurn = _pendingTurns[clientMessageId];
    if (pendingTurn == null) {
      return;
    }

    final existingIndex = _messages.indexWhere(
      (message) =>
          message.messageId == assistantMessageId ||
          (pendingTurn.assistantMessageId != null &&
              message.messageId == pendingTurn.assistantMessageId),
    );

    final nextMessages = [..._messages];
    final existingMessage = existingIndex >= 0
        ? nextMessages[existingIndex]
        : TranscriptMessage(
            messageId: assistantMessageId,
            role: 'assistant',
            text: '',
            createdAt: DateTime.now().millisecondsSinceEpoch / 1000,
            thinkingTrace: '',
            toolCalls: const [],
          );
    final updatedMessage = existingMessage.copyWith(
      messageId: assistantMessageId,
      text: text ?? existingMessage.text,
      thinkingTrace: thinkingTrace ?? existingMessage.thinkingTrace,
      toolCalls: toolCalls ?? existingMessage.toolCalls,
    );

    if (existingIndex >= 0) {
      nextMessages[existingIndex] = updatedMessage;
    } else {
      nextMessages.add(updatedMessage);
    }

    setState(() {
      _messages = nextMessages;
      _pendingTurns = {
        ..._pendingTurns,
        clientMessageId: pendingTurn.copyWith(
          assistantMessageId: assistantMessageId,
          seenToolCallIds: seenToolCallIds,
        ),
      };
    });
  }

  void _resetPendingTurnsForReplay() {
    if (_pendingTurns.isEmpty) {
      setState(() {
        _connectionState = _ChatConnectionState.offline;
        _connectionError ??= 'Disconnected from chat.';
      });
      return;
    }

    final assistantIdsToRemove = _pendingTurns.values
        .map((turn) => turn.assistantMessageId)
        .whereType<String>()
        .toSet();
    final nextMessages = _messages
        .where((message) => !assistantIdsToRemove.contains(message.messageId))
        .toList();
    final nextPendingTurns = <String, _PendingTurn>{};
    for (final entry in _pendingTurns.entries) {
      nextPendingTurns[entry.key] = entry.value.resetForReplay();
    }

    setState(() {
      _messages = nextMessages;
      _pendingTurns = nextPendingTurns;
      _clientMessageIdByTurnId = const {};
      _connectionState = _ChatConnectionState.offline;
    });
  }

  Future<void> _respondToApproval(bool approved) async {
    final approval = _pendingApproval;
    final socket = _chatSocket;
    if (approval == null) {
      return;
    }
    if (socket == null) {
      _showSnackBar(
        'Chat connection is offline. Approval will be retried once reconnects succeed.',
      );
      return;
    }

    setState(() {
      _submittingApproval = true;
    });

    try {
      await socket.send({
        'type': 'approval',
        'approval_id': approval.approvalId,
        'decision': approved ? 'approve' : 'deny',
      });
      if (!mounted) {
        return;
      }
      setState(() {
        _pendingApproval = null;
        _submittingApproval = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _submittingApproval = false;
      });
      _handleSocketClosed(socket, error: '$error');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 20,
        title: Text(
          _sessionTitle,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ),
      body: Column(
        children: [
          if (_connectionError != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.wifi_tethering_error_rounded,
                        color: Color(0xFFB42318),
                      ),
                      const SizedBox(width: 12),
                      Expanded(child: Text(_connectionError!)),
                    ],
                  ),
                ),
              ),
            ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null && _messages.isEmpty
                ? Padding(
                    padding: const EdgeInsets.all(20),
                    child: ErrorCard(message: _error!, onRetry: _loadMessages),
                  )
                : ListView.separated(
                    controller: _scrollController,
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                    itemBuilder: (context, index) {
                      final message = _messages[index];
                      return MessageBubble(
                        message: message,
                        isPending: _isAssistantMessagePending(message),
                      );
                    },
                    separatorBuilder: (context, index) =>
                        const SizedBox(height: 10),
                    itemCount: _messages.length,
                  ),
          ),
          SafeArea(
            top: false,
            child: Container(
              decoration: const BoxDecoration(
                color: Color(0xFFF3EEE6),
                border: Border(top: BorderSide(color: Color(0xFFDCCFBC))),
              ),
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (_pendingApproval != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: _ApprovalCard(
                        request: _pendingApproval!,
                        submitting: _submittingApproval,
                        onApprove: () => _respondToApproval(true),
                        onDeny: () => _respondToApproval(false),
                      ),
                    ),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      IconButton(
                        tooltip: 'Image',
                        onPressed: () => _showUnavailable('Image attachments'),
                        icon: const Icon(Icons.add_photo_alternate_outlined),
                      ),
                      IconButton(
                        tooltip: 'Voice',
                        onPressed: () => _showUnavailable('Voice input'),
                        icon: const Icon(Icons.graphic_eq),
                      ),
                      Expanded(
                        child: TextField(
                          controller: _composer,
                          minLines: 1,
                          maxLines: 5,
                          textInputAction: TextInputAction.newline,
                          decoration: InputDecoration(
                            labelText: _activeModelLabel ?? 'Active model',
                            floatingLabelBehavior: FloatingLabelBehavior.always,
                            filled: true,
                            fillColor: const Color(0xFFFFFBF5),
                            contentPadding: const EdgeInsets.symmetric(
                              horizontal: 18,
                              vertical: 14,
                            ),
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(24),
                              borderSide: const BorderSide(
                                color: Color(0xFFDCCFBC),
                              ),
                            ),
                            enabledBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(24),
                              borderSide: const BorderSide(
                                color: Color(0xFFDCCFBC),
                              ),
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      FilledButton(
                        onPressed: _sending ? null : _sendMessage,
                        style: FilledButton.styleFrom(
                          backgroundColor: const Color(0xFFB85C38),
                          foregroundColor: Colors.white,
                          minimumSize: const Size(56, 56),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(22),
                          ),
                        ),
                        child: _sending
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: Colors.white,
                                ),
                              )
                            : const Icon(Icons.arrow_upward),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.currentServerUrl,
    required this.settingsStore,
  });

  final String? currentServerUrl;
  final SettingsStore settingsStore;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _controller = TextEditingController(
    text: widget.currentServerUrl ?? '',
  );
  final TextEditingController _locationController = TextEditingController();
  final TextEditingController _personalInstructionsController =
      TextEditingController();

  bool _saving = false;
  bool _updatingModel = false;
  HealthStatus? _health;
  ModelSettings? _modelSettings;
  String? _selectedModelKey;
  String? _error;

  @override
  void initState() {
    super.initState();
    unawaited(_loadLocalSettings());
    final candidate = widget.currentServerUrl?.trim() ?? '';
    if (candidate.isNotEmpty) {
      unawaited(_loadServerState(candidate));
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    _locationController.dispose();
    _personalInstructionsController.dispose();
    super.dispose();
  }

  Future<void> _loadLocalSettings() async {
    final savedSettings = await widget.settingsStore.loadUserContextSettings();
    if (!mounted) {
      return;
    }
    _locationController.text = savedSettings.location;
    _personalInstructionsController.text = savedSettings.personalInstructions;
  }

  Future<void> _testConnection() async =>
      _loadServerState(_controller.text.trim());

  Future<void> _loadServerState(String candidate) async {
    if (candidate.isEmpty) {
      setState(() {
        _error = 'Enter a server URL first.';
      });
      return;
    }

    setState(() {
      _saving = true;
      _error = null;
    });

    try {
      final client = ConduitApiClient(baseUrl: candidate);
      final results = await Future.wait<Object>([
        client.health(),
        client.getModelSettings(),
      ]);
      if (!mounted || _controller.text.trim() != candidate) {
        return;
      }

      final health = results[0] as HealthStatus;
      final modelSettings = results[1] as ModelSettings;
      setState(() {
        _health = health;
        _modelSettings = modelSettings;
        _selectedModelKey = modelSettings.activeKey;
        _saving = false;
      });
    } catch (error) {
      if (!mounted || _controller.text.trim() != candidate) {
        return;
      }
      setState(() {
        _error = '$error';
        _health = null;
        _modelSettings = null;
        _selectedModelKey = null;
        _saving = false;
        _updatingModel = false;
      });
    }
  }

  Future<void> _save() async {
    final candidate = _controller.text.trim();
    final location = _locationController.text.trim();
    final personalInstructions = _personalInstructionsController.text.trim();
    if (candidate.isEmpty) {
      setState(() {
        _error = 'Server URL cannot be empty.';
      });
      return;
    }

    setState(() {
      _saving = true;
      _error = null;
    });

    try {
      await widget.settingsStore.saveUserContextSettings(
        location: location,
        personalInstructions: personalInstructions,
      );
      final client = ConduitApiClient(baseUrl: candidate);
      final results = await Future.wait<Object>([
        client.health(),
        client.getModelSettings(),
      ]);
      await widget.settingsStore.saveServerUrl(candidate);
      if (!mounted) {
        return;
      }

      final health = results[0] as HealthStatus;
      final modelSettings = results[1] as ModelSettings;
      setState(() {
        _health = health;
        _modelSettings = modelSettings;
        _selectedModelKey = modelSettings.activeKey;
        _saving = false;
      });

      Navigator.of(context).pop<String>(_controller.text.trim());
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Saved ${_controller.text.trim()}.')),
      );
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = '$error';
        _saving = false;
      });
    }
  }

  Future<void> _applyModelChange(String modelKey) async {
    final candidate = _controller.text.trim();
    final modelSettings = _modelSettings;
    if (candidate.isEmpty || modelSettings == null) {
      setState(() {
        _error = 'Connect to a server before changing the model.';
      });
      return;
    }
    if (modelKey == modelSettings.activeKey) {
      return;
    }

    final previousModelKey = modelSettings.activeKey;
    setState(() {
      _updatingModel = true;
      _error = null;
      _selectedModelKey = modelKey;
    });

    try {
      final client = ConduitApiClient(baseUrl: candidate);
      final updatedModelSettings = await client.updateModel(modelKey);
      final health = await client.health();
      if (!mounted) {
        return;
      }

      setState(() {
        _health = health;
        _modelSettings = updatedModelSettings;
        _selectedModelKey = updatedModelSettings.activeKey;
        _updatingModel = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Switched to ${updatedModelSettings.activeLabel}.'),
        ),
      );
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = '$error';
        _selectedModelKey = previousModelKey;
        _updatingModel = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Server URL', style: theme.textTheme.titleLarge),
                  const SizedBox(height: 8),
                  Text(
                    'Use your Tailscale IP and port, for example `http://100.x.y.z:18423`.',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: const Color(0xFF5B6672),
                    ),
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _controller,
                    keyboardType: TextInputType.url,
                    autocorrect: false,
                    onChanged: (_) {
                      setState(() {
                        _health = null;
                        _modelSettings = null;
                        _selectedModelKey = null;
                        _error = null;
                      });
                    },
                    decoration: InputDecoration(
                      hintText: 'http://100.x.y.z:18423',
                      filled: true,
                      fillColor: const Color(0xFFF8F3EB),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(20),
                        borderSide: const BorderSide(color: Color(0xFFDCCFBC)),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: (_saving || _updatingModel)
                              ? null
                              : _testConnection,
                          icon: const Icon(Icons.network_ping),
                          label: const Text('Test'),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: FilledButton.icon(
                          onPressed: (_saving || _updatingModel) ? null : _save,
                          icon: _saving
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Icon(Icons.save_alt),
                          label: const Text('Save'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 24),
                  Text('Location', style: theme.textTheme.titleMedium),
                  const SizedBox(height: 8),
                  Text(
                    'This is added to the hidden model context on every turn.',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: const Color(0xFF5B6672),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _locationController,
                    textInputAction: TextInputAction.next,
                    decoration: InputDecoration(
                      hintText: 'Zurich, Switzerland',
                      filled: true,
                      fillColor: const Color(0xFFF8F3EB),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(20),
                        borderSide: const BorderSide(color: Color(0xFFDCCFBC)),
                      ),
                    ),
                  ),
                  const SizedBox(height: 20),
                  Text(
                    'Personal instructions',
                    style: theme.textTheme.titleMedium,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Use this for stable preferences you want Conduit to remember.',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: const Color(0xFF5B6672),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _personalInstructionsController,
                    minLines: 4,
                    maxLines: 8,
                    decoration: InputDecoration(
                      hintText:
                          'Example: keep answers concise and prefer Swiss context when relevant.',
                      filled: true,
                      fillColor: const Color(0xFFF8F3EB),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(20),
                        borderSide: const BorderSide(color: Color(0xFFDCCFBC)),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          if (_modelSettings != null) ...[
            const SizedBox(height: 16),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Base model', style: theme.textTheme.titleMedium),
                    const SizedBox(height: 8),
                    Text(
                      'This setting is stored on the server and applies to future turns.',
                      style: theme.textTheme.bodyMedium?.copyWith(
                        color: const Color(0xFF5B6672),
                      ),
                    ),
                    if (_updatingModel) ...[
                      const SizedBox(height: 12),
                      const LinearProgressIndicator(minHeight: 3),
                    ],
                    const SizedBox(height: 16),
                    DropdownButtonFormField<String>(
                      key: ValueKey(
                        '${_modelSettings!.activeKey}:${_selectedModelKey ?? ''}',
                      ),
                      initialValue: _selectedModelKey,
                      decoration: InputDecoration(
                        filled: true,
                        fillColor: const Color(0xFFF8F3EB),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(20),
                          borderSide: const BorderSide(
                            color: Color(0xFFDCCFBC),
                          ),
                        ),
                      ),
                      items: _modelSettings!.options
                          .map(
                            (option) => DropdownMenuItem<String>(
                              value: option.key,
                              enabled: option.available,
                              child: Text(
                                option.available
                                    ? option.label
                                    : '${option.label} (missing API key)',
                              ),
                            ),
                          )
                          .toList(),
                      onChanged: (_saving || _updatingModel)
                          ? null
                          : (value) {
                              if (value == null) {
                                return;
                              }
                              unawaited(_applyModelChange(value));
                            },
                    ),
                  ],
                ),
              ),
            ),
          ],
          if (_health != null) ...[
            const SizedBox(height: 16),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Connection looks good',
                      style: theme.textTheme.titleMedium,
                    ),
                    const SizedBox(height: 10),
                    Text('App: ${_health!.appName}'),
                    Text(
                      'Model: ${_health!.modelLabel.isNotEmpty ? _health!.modelLabel : _health!.model}',
                    ),
                    Text('Provider: ${_health!.provider}'),
                  ],
                ),
              ),
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 16),
            ErrorCard(message: _error!, onRetry: _testConnection),
          ],
        ],
      ),
    );
  }
}

class EmptyServerCard extends StatelessWidget {
  const EmptyServerCard({super.key, required this.onConfigure});

  final VoidCallback onConfigure;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Point Conduit at your server.',
              style: Theme.of(context).textTheme.headlineSmall,
            ),
            const SizedBox(height: 12),
            Text(
              'This client is intentionally thin. Set the Conduit FastAPI URL first, then create or resume sessions from here.',
              style: Theme.of(
                context,
              ).textTheme.bodyLarge?.copyWith(color: const Color(0xFF5B6672)),
            ),
            const SizedBox(height: 18),
            FilledButton.icon(
              onPressed: onConfigure,
              icon: const Icon(Icons.settings_ethernet),
              label: const Text('Configure server'),
            ),
          ],
        ),
      ),
    );
  }
}

class EmptySessionsCard extends StatelessWidget {
  const EmptySessionsCard({super.key});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'No sessions yet.',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 8),
            Text(
              'Start a new session and this screen will become your recent conversation index.',
              style: Theme.of(
                context,
              ).textTheme.bodyLarge?.copyWith(color: const Color(0xFF5B6672)),
            ),
          ],
        ),
      ),
    );
  }
}

class ErrorCard extends StatelessWidget {
  const ErrorCard({super.key, required this.message, required this.onRetry});

  final String message;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Request failed',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 8),
            Text(message),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
            ),
          ],
        ),
      ),
    );
  }
}

class SessionCard extends StatelessWidget {
  const SessionCard({super.key, required this.session, required this.onTap});

  final SessionSummary session;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(28),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Row(
            children: [
              Container(
                width: 52,
                height: 52,
                decoration: BoxDecoration(
                  color: const Color(0xFF184E77),
                  borderRadius: BorderRadius.circular(18),
                ),
                child: const Icon(Icons.forum_outlined, color: Colors.white),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      session.title,
                      style: Theme.of(context).textTheme.titleMedium,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      _sessionMeta(session),
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: const Color(0xFF5B6672),
                      ),
                    ),
                  ],
                ),
              ),
              const Icon(Icons.arrow_forward_ios, size: 16),
            ],
          ),
        ),
      ),
    );
  }
}

class MessageBubble extends StatelessWidget {
  const MessageBubble({
    super.key,
    required this.message,
    this.isPending = false,
  });

  final TranscriptMessage message;
  final bool isPending;

  @override
  Widget build(BuildContext context) {
    final isUser = message.isUser;
    final alignment = isUser
        ? CrossAxisAlignment.end
        : CrossAxisAlignment.start;
    final bubbleColor = isUser
        ? const Color(0xFF184E77)
        : const Color(0xFFFFFBF5);
    final foregroundColor = isUser ? Colors.white : const Color(0xFF16202A);
    final bubbleRadius = BorderRadius.only(
      topLeft: const Radius.circular(24),
      topRight: const Radius.circular(24),
      bottomLeft: Radius.circular(isUser ? 24 : 6),
      bottomRight: Radius.circular(isUser ? 6 : 24),
    );

    return Column(
      crossAxisAlignment: alignment,
      children: [
        Text(
          isUser ? 'You' : 'Conduit',
          style: Theme.of(
            context,
          ).textTheme.labelMedium?.copyWith(color: const Color(0xFF6B7280)),
        ),
        const SizedBox(height: 6),
        Container(
          constraints: const BoxConstraints(maxWidth: 520),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
            color: bubbleColor,
            borderRadius: bubbleRadius,
            border: isUser ? null : Border.all(color: const Color(0xFFE6DDCF)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (!isUser && message.thinkingTrace.isNotEmpty) ...[
                ThoughtTraceCard(trace: message.thinkingTrace),
                if (message.text.isNotEmpty ||
                    message.toolCalls.isNotEmpty ||
                    isPending)
                  const SizedBox(height: 10),
              ],
              if (message.text.isNotEmpty)
                isUser
                    ? SelectableText(
                        message.text,
                        style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                          color: foregroundColor,
                          height: 1.45,
                        ),
                      )
                    : MarkdownBody(
                        data: message.text,
                        selectable: true,
                        fitContent: true,
                        styleSheet: _assistantMarkdownStyleSheet(
                          context,
                          foregroundColor,
                        ),
                      ),
              if (message.toolCalls.isNotEmpty) ...[
                if (message.text.isNotEmpty) const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: message.toolCalls
                      .map(
                        (toolCall) =>
                            ToolChip(toolCall: toolCall, isUser: isUser),
                      )
                      .toList(),
                ),
              ],
              if (isPending) ...[
                if (message.text.isNotEmpty || message.toolCalls.isNotEmpty)
                  const SizedBox(height: 10),
                PendingReplyIndicator(
                  color: isUser ? Colors.white70 : const Color(0xFF7B4B2A),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 6),
        Text(
          _formatTimestamp(message.createdAt),
          style: Theme.of(
            context,
          ).textTheme.labelSmall?.copyWith(color: const Color(0xFF6B7280)),
        ),
      ],
    );
  }
}

class _ApprovalCard extends StatelessWidget {
  const _ApprovalCard({
    required this.request,
    required this.submitting,
    required this.onApprove,
    required this.onDeny,
  });

  final _PendingApprovalRequest request;
  final bool submitting;
  final Future<void> Function() onApprove;
  final Future<void> Function() onDeny;

  @override
  Widget build(BuildContext context) {
    final detailLabel = _approvalDetailLabel(request);
    final detailText = _approvalDetailText(request);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: const Color(0xFFFFE2D6),
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: const Icon(Icons.verified_user_outlined),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    'Approval required for ${request.tool}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(request.summary),
            if (detailText != null) ...[
              const SizedBox(height: 14),
              Text(
                detailLabel,
                style: Theme.of(context).textTheme.labelLarge?.copyWith(
                  color: const Color(0xFF7B4B2A),
                ),
              ),
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                constraints: const BoxConstraints(maxHeight: 220),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: const Color(0xFFF8F3EB),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: const Color(0xFFE6DDCF)),
                ),
                child: SingleChildScrollView(
                  child: SelectableText(
                    detailText,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      fontFamily: 'monospace',
                      height: 1.45,
                    ),
                  ),
                ),
              ),
            ],
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: submitting ? null : onDeny,
                    child: const Text('Deny'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    onPressed: submitting ? null : onApprove,
                    child: submitting
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Text('Approve'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class ThoughtTraceCard extends StatefulWidget {
  const ThoughtTraceCard({super.key, required this.trace});

  final String trace;

  @override
  State<ThoughtTraceCard> createState() => _ThoughtTraceCardState();
}

class _ThoughtTraceCardState extends State<ThoughtTraceCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        color: const Color(0xFFF8F3EB),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: const Color(0xFFE6DDCF)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            borderRadius: BorderRadius.circular(20),
            onTap: () {
              setState(() {
                _expanded = !_expanded;
              });
            },
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              child: Row(
                children: [
                  const Icon(
                    Icons.psychology_alt_outlined,
                    size: 18,
                    color: Color(0xFF7B4B2A),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Thinking trace',
                      style: theme.textTheme.labelLarge?.copyWith(
                        color: const Color(0xFF7B4B2A),
                      ),
                    ),
                  ),
                  Icon(
                    _expanded ? Icons.expand_less : Icons.expand_more,
                    color: const Color(0xFF7B4B2A),
                  ),
                ],
              ),
            ),
          ),
          AnimatedCrossFade(
            firstChild: const SizedBox.shrink(),
            secondChild: Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
              child: SelectableText(
                widget.trace,
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: const Color(0xFF5B6672),
                  height: 1.5,
                ),
              ),
            ),
            crossFadeState: _expanded
                ? CrossFadeState.showSecond
                : CrossFadeState.showFirst,
            duration: const Duration(milliseconds: 180),
          ),
        ],
      ),
    );
  }
}

String _sessionMeta(SessionSummary session) {
  final timestamp = _formatTimestamp(session.lastUpdateTime);
  if (session.eventCount <= 0) {
    return timestamp;
  }
  final label = session.eventCount == 1
      ? '1 event'
      : '${session.eventCount} events';
  return '$timestamp • $label';
}

String? _deriveSessionTitle(List<TranscriptMessage> messages) {
  for (final message in messages) {
    if (!message.isUser) {
      continue;
    }
    final title = _deriveTitleFromText(message.text);
    if (title.isNotEmpty) {
      return title;
    }
  }
  return null;
}

String _deriveTitleFromText(String text) {
  final normalized = text.replaceAll('\n', ' ').trim();
  return normalized.split(RegExp(r'\s+')).join(' ');
}

MarkdownStyleSheet _assistantMarkdownStyleSheet(
  BuildContext context,
  Color foregroundColor,
) {
  final theme = Theme.of(context);
  final bodyStyle = theme.textTheme.bodyLarge?.copyWith(
    color: foregroundColor,
    height: 1.45,
  );
  return MarkdownStyleSheet.fromTheme(theme).copyWith(
    p: bodyStyle,
    h1: theme.textTheme.headlineSmall?.copyWith(
      color: foregroundColor,
      height: 1.2,
    ),
    h2: theme.textTheme.titleLarge?.copyWith(
      color: foregroundColor,
      height: 1.25,
    ),
    h3: theme.textTheme.titleMedium?.copyWith(
      color: foregroundColor,
      height: 1.3,
    ),
    blockSpacing: 12,
    blockquote: bodyStyle,
    blockquotePadding: const EdgeInsets.all(12),
    blockquoteDecoration: BoxDecoration(
      color: const Color(0xFFF6EFE2),
      borderRadius: BorderRadius.circular(16),
      border: Border.all(color: const Color(0xFFE6DDCF)),
    ),
    code: theme.textTheme.bodyMedium?.copyWith(
      color: foregroundColor,
      fontFamily: 'monospace',
      fontSize: (theme.textTheme.bodyMedium?.fontSize ?? 14) * 0.92,
    ),
    codeblockPadding: const EdgeInsets.all(12),
    codeblockDecoration: BoxDecoration(
      color: const Color(0xFFF6EFE2),
      borderRadius: BorderRadius.circular(16),
      border: Border.all(color: const Color(0xFFE6DDCF)),
    ),
    horizontalRuleDecoration: const BoxDecoration(
      border: Border(top: BorderSide(color: Color(0xFFE6DDCF), width: 1.5)),
    ),
    listBullet: bodyStyle,
    a: theme.textTheme.bodyLarge?.copyWith(
      color: const Color(0xFF184E77),
      decoration: TextDecoration.underline,
      height: 1.45,
    ),
  );
}

class ToolChip extends StatelessWidget {
  const ToolChip({super.key, required this.toolCall, required this.isUser});

  final ToolCall toolCall;
  final bool isUser;

  @override
  Widget build(BuildContext context) {
    final isFailed = toolCall.isFailed;
    final detail = _toolCallDetail(toolCall);
    final chipColor = isUser
        ? Colors.white.withValues(alpha: 0.16)
        : isFailed
        ? const Color(0xFFFFE7E5)
        : const Color(0xFFF8F3EB);
    final borderColor = isUser
        ? Colors.white24
        : isFailed
        ? const Color(0xFFF2B8B5)
        : const Color(0xFFDCCFBC);
    final foregroundColor = isUser
        ? Colors.white
        : isFailed
        ? const Color(0xFFB42318)
        : const Color(0xFF7B4B2A);

    return Tooltip(
      message: toolCall.error ?? detail ?? _toolCallLabel(toolCall),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 340),
        child: Container(
          padding: EdgeInsets.symmetric(
            horizontal: 10,
            vertical: detail == null ? 8 : 10,
          ),
          decoration: BoxDecoration(
            color: chipColor,
            borderRadius: BorderRadius.circular(detail == null ? 999 : 18),
            border: Border.all(color: borderColor),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    isFailed ? Icons.error_outline : Icons.build_outlined,
                    size: 14,
                    color: isUser ? Colors.white70 : foregroundColor,
                  ),
                  const SizedBox(width: 6),
                  Flexible(
                    child: Text(
                      _toolCallLabel(toolCall),
                      style: Theme.of(
                        context,
                      ).textTheme.labelMedium?.copyWith(color: foregroundColor),
                    ),
                  ),
                ],
              ),
              if (detail != null) ...[
                const SizedBox(height: 8),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: isUser
                        ? Colors.white.withValues(alpha: 0.08)
                        : const Color(0xFFFFFBF5),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                      color: isUser ? Colors.white12 : const Color(0xFFE6DDCF),
                    ),
                  ),
                  child: SelectableText(
                    detail,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: isUser ? Colors.white : const Color(0xFF5B6672),
                      fontFamily: 'monospace',
                      height: 1.45,
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class PendingReplyIndicator extends StatefulWidget {
  const PendingReplyIndicator({super.key, required this.color});

  final Color color;

  @override
  State<PendingReplyIndicator> createState() => _PendingReplyIndicatorState();
}

class _PendingReplyIndicatorState extends State<PendingReplyIndicator>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        final phase = (_controller.value * 3).floor() % 3;
        final dots = '.' * (phase + 1);
        return Text(
          dots,
          style: Theme.of(context).textTheme.headlineSmall?.copyWith(
            color: widget.color,
            letterSpacing: 2,
          ),
        );
      },
    );
  }
}

String _toolCallLabel(ToolCall toolCall) {
  switch (toolCall.name) {
    case 'bash':
      final command = _ellipsize(
        (toolCall.args['command'] as String?)?.trim() ?? 'command',
        30,
      );
      return 'bash($command)';
    case 'web_search':
      final query = _ellipsize(
        (toolCall.args['query'] as String?)?.trim() ?? 'query',
        38,
      );
      return 'web_search($query)';
    case 'web_fetch':
      final url = (toolCall.args['url'] as String?)?.trim() ?? 'url';
      return 'web_fetch(${_shortenUrl(url)})';
    default:
      return toolCall.name;
  }
}

String? _toolCallDetail(ToolCall toolCall) {
  if (toolCall.name != 'bash') {
    return null;
  }

  final sections = <String>[];
  final meta = <String>[];
  final command = (toolCall.args['command'] as String?)?.trim();
  if (command != null && command.isNotEmpty) {
    sections.add('command\n$command');
  }

  final response = toolCall.response;
  if (response == null || response.isEmpty) {
    return sections.isNotEmpty ? sections.join('\n\n') : toolCall.error;
  }

  final exitCode = response['exit_code'];
  if (exitCode != null) {
    meta.add('exit $exitCode');
  }
  if (response['timed_out'] == true) {
    meta.add('timed out');
  }
  final duration = response['duration_seconds'];
  if (duration is num) {
    meta.add('${duration.toStringAsFixed(3)}s');
  }
  final workingDirectory = (response['working_directory'] as String?)?.trim();
  if (workingDirectory != null && workingDirectory.isNotEmpty) {
    meta.add('cwd ${_ellipsize(workingDirectory, 42)}');
  }
  if (meta.isNotEmpty) {
    sections.add(meta.join(' • '));
  }

  final stdout = (response['stdout'] as String?) ?? '';
  if (stdout.isNotEmpty) {
    var stdoutSection = 'stdout\n$stdout';
    if (response['stdout_truncated'] == true) {
      stdoutSection = '$stdoutSection\n[truncated]';
    }
    sections.add(stdoutSection);
  }

  final stderr = (response['stderr'] as String?) ?? '';
  if (stderr.isNotEmpty) {
    var stderrSection = 'stderr\n$stderr';
    if (response['stderr_truncated'] == true) {
      stderrSection = '$stderrSection\n[truncated]';
    }
    sections.add(stderrSection);
  }

  final error = (response['error'] as String?)?.trim();
  if (error != null && error.isNotEmpty && stderr.isEmpty) {
    sections.add('error\n$error');
  }

  if (sections.isEmpty) {
    return 'No stdout or stderr.';
  }
  return sections.join('\n\n');
}

String _shortenUrl(String url) {
  try {
    final uri = Uri.parse(url);
    final host = uri.host.isEmpty ? url : uri.host;
    final path = uri.path == '/' ? '' : uri.path;
    final compact = '$host$path';
    return _ellipsize(compact, 34);
  } catch (_) {
    return _ellipsize(url, 34);
  }
}

String _ellipsize(String value, int maxLength) {
  if (value.length <= maxLength) {
    return value;
  }
  return '${value.substring(0, maxLength - 1)}…';
}

class StatusBadge extends StatelessWidget {
  const StatusBadge({super.key, required this.connected, required this.label});

  final bool connected;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(right: 8),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: connected ? const Color(0xFFD6F5DF) : const Color(0xFFF7D9D9),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.circle,
            size: 10,
            color: connected
                ? const Color(0xFF117A37)
                : const Color(0xFFB42318),
          ),
          const SizedBox(width: 8),
          Text(label),
        ],
      ),
    );
  }
}

enum _ChatConnectionState { connecting, connected, reconnecting, offline }

class _PendingTurn {
  const _PendingTurn({
    required this.clientMessageId,
    required this.text,
    required this.context,
    required this.userMessageId,
    this.turnId,
    this.assistantMessageId,
    this.seenToolCallIds = const [],
  });

  final String clientMessageId;
  final String text;
  final _TurnContextPayload context;
  final String userMessageId;
  final String? turnId;
  final String? assistantMessageId;
  final List<String> seenToolCallIds;

  _PendingTurn copyWith({
    String? turnId,
    String? assistantMessageId,
    List<String>? seenToolCallIds,
  }) {
    return _PendingTurn(
      clientMessageId: clientMessageId,
      text: text,
      context: context,
      userMessageId: userMessageId,
      turnId: turnId ?? this.turnId,
      assistantMessageId: assistantMessageId ?? this.assistantMessageId,
      seenToolCallIds: seenToolCallIds ?? this.seenToolCallIds,
    );
  }

  _PendingTurn resetForReplay() {
    return _PendingTurn(
      clientMessageId: clientMessageId,
      text: text,
      context: context,
      userMessageId: userMessageId,
    );
  }
}

class _TurnContextPayload {
  const _TurnContextPayload({
    required this.currentTime,
    required this.location,
    required this.personalInstructions,
  });

  final String currentTime;
  final String location;
  final String personalInstructions;

  Map<String, dynamic> toJson() => {
    'current_time': currentTime,
    if (location.isNotEmpty) 'location': location,
    if (personalInstructions.isNotEmpty)
      'personal_instructions': personalInstructions,
  };
}

class _PendingApprovalRequest {
  const _PendingApprovalRequest({
    required this.approvalId,
    required this.turnId,
    required this.tool,
    required this.args,
    required this.summary,
  });

  final String approvalId;
  final String turnId;
  final String tool;
  final Map<String, dynamic> args;
  final String summary;
}

String _approvalDetailLabel(_PendingApprovalRequest request) {
  if (request.tool == 'bash') {
    return 'Full command';
  }
  return 'Arguments';
}

String? _approvalDetailText(_PendingApprovalRequest request) {
  if (request.args.isEmpty) {
    return null;
  }
  if (request.tool == 'bash') {
    final command = (request.args['command'] as String?)?.trim();
    if (command != null && command.isNotEmpty) {
      return command;
    }
  }
  return const JsonEncoder.withIndent('  ').convert(request.args);
}

String _makeClientMessageId() {
  final microseconds = DateTime.now().microsecondsSinceEpoch;
  final entropy = math.Random().nextInt(1 << 20).toRadixString(16);
  return 'm_${microseconds}_$entropy';
}

String _formatCurrentTimeForContext(DateTime value) {
  final local = value.toLocal();
  final offset = local.timeZoneOffset;
  final sign = offset.isNegative ? '-' : '+';
  final absoluteOffset = offset.abs();
  final offsetHours = absoluteOffset.inHours;
  final offsetMinutes = absoluteOffset.inMinutes.remainder(60);
  final timezoneName = local.timeZoneName.trim();
  final offsetLabel =
      'UTC$sign${_twoDigits(offsetHours)}:${_twoDigits(offsetMinutes)}';
  final timezoneLabel = timezoneName.isEmpty
      ? offsetLabel
      : '$timezoneName ($offsetLabel)';
  return '${local.year}-${_twoDigits(local.month)}-${_twoDigits(local.day)} '
      '${_twoDigits(local.hour)}:${_twoDigits(local.minute)}:${_twoDigits(local.second)} '
      '$timezoneLabel';
}

String _twoDigits(int value) => value.toString().padLeft(2, '0');

String _makeDraftSessionId() {
  final microseconds = DateTime.now().microsecondsSinceEpoch;
  final entropy = math.Random().nextInt(1 << 20).toRadixString(16);
  return 's_${microseconds}_$entropy';
}

String _formatTimestamp(double seconds) {
  final value = DateTime.fromMillisecondsSinceEpoch(
    (seconds * 1000).round(),
  ).toLocal();
  final now = DateTime.now();
  final hh = value.hour.toString().padLeft(2, '0');
  final mm = value.minute.toString().padLeft(2, '0');

  final isToday =
      value.year == now.year &&
      value.month == now.month &&
      value.day == now.day;
  if (isToday) {
    return '$hh:$mm';
  }

  final month = value.month.toString().padLeft(2, '0');
  final day = value.day.toString().padLeft(2, '0');
  return '${value.year}-$month-$day $hh:$mm';
}

String? _fallbackServerUrl() {
  final value = _defaultServerUrl.trim();
  if (value.isEmpty) {
    return null;
  }
  return value;
}

String? _resolveInitialServerUrl({required String? storedServerUrl}) {
  final definedServerUrl = _fallbackServerUrl();
  if (definedServerUrl != null) {
    return definedServerUrl;
  }
  return storedServerUrl;
}
