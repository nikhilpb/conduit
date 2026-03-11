class HealthStatus {
  const HealthStatus({
    required this.ok,
    required this.appName,
    required this.model,
    required this.modelLabel,
    required this.provider,
  });

  factory HealthStatus.fromJson(Map<String, dynamic> json) {
    return HealthStatus(
      ok: json['ok'] as bool? ?? false,
      appName: json['app_name'] as String? ?? 'Conduit',
      model: json['model'] as String? ?? '',
      modelLabel: json['model_label'] as String? ?? '',
      provider: json['provider'] as String? ?? '',
    );
  }

  final bool ok;
  final String appName;
  final String model;
  final String modelLabel;
  final String provider;
}

class SessionSummary {
  const SessionSummary({
    required this.sessionId,
    required this.lastUpdateTime,
    required this.eventCount,
    required this.title,
  });

  factory SessionSummary.fromJson(Map<String, dynamic> json) {
    return SessionSummary(
      sessionId: json['session_id'] as String,
      lastUpdateTime: (json['last_update_time'] as num?)?.toDouble() ?? 0,
      eventCount: json['event_count'] as int? ?? 0,
      title: json['title'] as String? ?? '',
    );
  }

  final String sessionId;
  final double lastUpdateTime;
  final int eventCount;
  final String title;
}

const Set<String> _hiddenToolCallNames = {'adk_request_confirmation'};

bool isVisibleToolCallName(String? name) {
  return name != null && !_hiddenToolCallNames.contains(name);
}

class ToolCall {
  const ToolCall({
    this.toolCallId,
    required this.name,
    required this.args,
    this.status = 'pending',
    this.error,
    this.response,
  });

  factory ToolCall.fromJson(Map<String, dynamic> json) {
    return ToolCall(
      toolCallId: json['tool_call_id'] as String?,
      name: json['name'] as String? ?? 'tool',
      args: Map<String, dynamic>.from(json['args'] as Map? ?? const {}),
      status: json['status'] as String? ?? 'pending',
      error: json['error'] as String?,
      response: json['response'] == null
          ? null
          : Map<String, dynamic>.from(json['response'] as Map),
    );
  }

  final String? toolCallId;
  final String name;
  final Map<String, dynamic> args;
  final String status;
  final String? error;
  final Map<String, dynamic>? response;

  bool get isFailed => status == 'failed';
  bool get isVisible => isVisibleToolCallName(name);

  Map<String, dynamic> toJson() => {
    'tool_call_id': toolCallId,
    'name': name,
    'args': args,
    'status': status,
    'error': error,
    'response': response,
  };
}

class TranscriptMessage {
  const TranscriptMessage({
    required this.messageId,
    required this.role,
    required this.text,
    required this.createdAt,
    required this.thinkingTrace,
    required this.toolCalls,
  });

  factory TranscriptMessage.fromJson(Map<String, dynamic> json) {
    return TranscriptMessage(
      messageId: json['message_id'] as String? ?? '',
      role: json['role'] as String? ?? 'assistant',
      text: json['text'] as String? ?? '',
      createdAt: (json['created_at'] as num?)?.toDouble() ?? 0,
      thinkingTrace: json['thinking_trace'] as String? ?? '',
      toolCalls: (json['tool_calls'] as List<dynamic>? ?? const [])
          .map((item) => ToolCall.fromJson(item as Map<String, dynamic>))
          .where((toolCall) => toolCall.isVisible)
          .toList(),
    );
  }

  final String messageId;
  final String role;
  final String text;
  final double createdAt;
  final String thinkingTrace;
  final List<ToolCall> toolCalls;

  bool get isUser => role == 'user';

  TranscriptMessage copyWith({
    String? messageId,
    String? role,
    String? text,
    double? createdAt,
    String? thinkingTrace,
    List<ToolCall>? toolCalls,
  }) {
    return TranscriptMessage(
      messageId: messageId ?? this.messageId,
      role: role ?? this.role,
      text: text ?? this.text,
      createdAt: createdAt ?? this.createdAt,
      thinkingTrace: thinkingTrace ?? this.thinkingTrace,
      toolCalls: toolCalls ?? this.toolCalls,
    );
  }
}

class SessionDetail {
  const SessionDetail({required this.sessionId, required this.messages});

  factory SessionDetail.fromJson(Map<String, dynamic> json) {
    return SessionDetail(
      sessionId: json['session_id'] as String,
      messages: (json['messages'] as List<dynamic>? ?? const [])
          .map(
            (item) => TranscriptMessage.fromJson(item as Map<String, dynamic>),
          )
          .toList(),
    );
  }

  final String sessionId;
  final List<TranscriptMessage> messages;
}

class ChatReply {
  const ChatReply({
    required this.sessionId,
    required this.reply,
    required this.toolCalls,
  });

  factory ChatReply.fromJson(Map<String, dynamic> json) {
    return ChatReply(
      sessionId: json['session_id'] as String,
      reply: json['reply'] as String? ?? '',
      toolCalls: (json['tool_calls'] as List<dynamic>? ?? const [])
          .map((item) => ToolCall.fromJson(item as Map<String, dynamic>))
          .where((toolCall) => toolCall.isVisible)
          .toList(),
    );
  }

  final String sessionId;
  final String reply;
  final List<ToolCall> toolCalls;
}

class ChatServerEvent {
  const ChatServerEvent({
    required this.type,
    this.messageId,
    this.sessionId,
    this.turnId,
    this.approvalId,
    this.content,
    this.agent,
    this.toolCallId,
    this.tool,
    this.args = const {},
    this.permission,
    this.clientRequestId,
    this.summary,
    this.message,
    this.status,
    this.error,
    this.response,
  });

  factory ChatServerEvent.fromJson(Map<String, dynamic> json) {
    return ChatServerEvent(
      type: json['type'] as String? ?? 'error',
      messageId: json['message_id'] as String?,
      sessionId: json['session_id'] as String?,
      turnId: json['turn_id'] as String?,
      approvalId: json['approval_id'] as String?,
      content: json['content'] as String?,
      agent: json['agent'] as String?,
      toolCallId: json['tool_call_id'] as String?,
      tool: json['tool'] as String?,
      args: Map<String, dynamic>.from(json['args'] as Map? ?? const {}),
      permission: json['permission'] as String?,
      clientRequestId: json['client_request_id'] as String?,
      summary: json['summary'] as String?,
      message: json['message'] as String?,
      status: json['status'] as String?,
      error: json['error'] as String?,
      response: json['response'] == null
          ? null
          : Map<String, dynamic>.from(json['response'] as Map),
    );
  }

  final String type;
  final String? messageId;
  final String? sessionId;
  final String? turnId;
  final String? approvalId;
  final String? content;
  final String? agent;
  final String? toolCallId;
  final String? tool;
  final Map<String, dynamic> args;
  final String? permission;
  final String? clientRequestId;
  final String? summary;
  final String? message;
  final String? status;
  final String? error;
  final Map<String, dynamic>? response;

  bool get isTerminal => type == 'done' || type == 'error';
}

class ModelOption {
  const ModelOption({
    required this.key,
    required this.label,
    required this.model,
    required this.provider,
    required this.available,
  });

  factory ModelOption.fromJson(Map<String, dynamic> json) {
    return ModelOption(
      key: json['key'] as String? ?? '',
      label: json['label'] as String? ?? '',
      model: json['model'] as String? ?? '',
      provider: json['provider'] as String? ?? '',
      available: json['available'] as bool? ?? false,
    );
  }

  final String key;
  final String label;
  final String model;
  final String provider;
  final bool available;
}

class ModelSettings {
  const ModelSettings({
    required this.activeKey,
    required this.activeModel,
    required this.activeLabel,
    required this.provider,
    required this.options,
  });

  factory ModelSettings.fromJson(Map<String, dynamic> json) {
    return ModelSettings(
      activeKey: json['active_key'] as String? ?? '',
      activeModel: json['active_model'] as String? ?? '',
      activeLabel: json['active_label'] as String? ?? '',
      provider: json['provider'] as String? ?? '',
      options: (json['options'] as List<dynamic>? ?? const [])
          .map((item) => ModelOption.fromJson(item as Map<String, dynamic>))
          .toList(),
    );
  }

  final String activeKey;
  final String activeModel;
  final String activeLabel;
  final String provider;
  final List<ModelOption> options;
}
