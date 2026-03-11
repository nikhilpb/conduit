import 'package:flutter_test/flutter_test.dart';
import 'package:think_client/models.dart';

void main() {
  test(
    'session transcript hides approval-only entries and bash result-only entries',
    () {
      final detail = SessionDetail.fromJson({
        'session_id': 'session-1',
        'messages': [
          {
            'message_id': 'user-1',
            'role': 'user',
            'text': 'use bash to print hello world',
            'created_at': 1,
            'thinking_trace': '',
            'tool_calls': const [],
          },
          {
            'message_id': 'assistant-approval',
            'role': 'assistant',
            'text': '',
            'created_at': 2,
            'thinking_trace': '',
            'tool_calls': [
              {
                'tool_call_id': 'approval-call',
                'name': 'adk_request_confirmation',
                'args': {
                  'originalFunctionCall': {
                    'name': 'bash',
                    'args': {'command': 'echo "hello world"'},
                  },
                },
                'status': 'pending',
              },
            ],
          },
          {
            'message_id': 'user-approval',
            'role': 'user',
            'text': '',
            'created_at': 3,
            'thinking_trace': '',
            'tool_calls': [
              {
                'tool_call_id': 'approval-call',
                'name': 'adk_request_confirmation',
                'args': const {},
                'status': 'completed',
                'response': {'confirmed': true},
              },
            ],
          },
          {
            'message_id': 'assistant-bash-call',
            'role': 'assistant',
            'text': '',
            'created_at': 4,
            'thinking_trace': '',
            'tool_calls': [
              {
                'tool_call_id': 'bash-1',
                'name': 'bash',
                'args': {'command': 'echo "hello world"'},
                'status': 'pending',
              },
            ],
          },
          {
            'message_id': 'assistant-bash-result',
            'role': 'assistant',
            'text': '',
            'created_at': 5,
            'thinking_trace': '',
            'tool_calls': [
              {
                'tool_call_id': 'bash-1',
                'name': 'bash',
                'args': const {},
                'status': 'completed',
                'response': {'stdout': 'hello world', 'exit_code': 0},
              },
            ],
          },
          {
            'message_id': 'assistant-reply',
            'role': 'assistant',
            'text': 'The command was executed successfully.',
            'created_at': 6,
            'thinking_trace': '',
            'tool_calls': const [],
          },
        ],
      });

      expect(detail.messages, hasLength(3));
      expect(detail.messages[0].messageId, 'user-1');
      expect(detail.messages[1].messageId, 'assistant-bash-call');
      expect(detail.messages[1].toolCalls, hasLength(1));
      expect(detail.messages[1].toolCalls.single.name, 'bash');
      expect(
        detail.messages[1].toolCalls.single.args['command'],
        'echo "hello world"',
      );
      expect(detail.messages[2].messageId, 'assistant-reply');
    },
  );
}
