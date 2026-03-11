import 'dart:convert';
import 'dart:math' as math;

const double defaultContextCharsPerToken = 4.0;
const int lowContextUsageThresholdTokens = 10000;
const int highContextUsageThresholdTokens = 40000;
const int maxContextUsageBarTokens = 100000;

class ContextEstimate {
  const ContextEstimate({
    required this.chars,
    required this.tokens,
    required this.charsPerToken,
  });

  factory ContextEstimate.fromJson(Map<String, dynamic> json) {
    return ContextEstimate(
      chars: (json['chars'] as num?)?.toInt() ?? 0,
      tokens: (json['tokens'] as num?)?.toInt() ?? 0,
      charsPerToken:
          (json['chars_per_token'] as num?)?.toDouble() ??
          defaultContextCharsPerToken,
    );
  }

  static const empty = ContextEstimate(
    chars: 0,
    tokens: 0,
    charsPerToken: defaultContextCharsPerToken,
  );

  final int chars;
  final int tokens;
  final double charsPerToken;
}

enum ContextUsageLevel { low, medium, high }

int estimateTokensFromChars(
  int chars, {
  double charsPerToken = defaultContextCharsPerToken,
}) {
  if (chars <= 0) {
    return 0;
  }
  return (chars / charsPerToken).ceil();
}

String formatCurrentTimeForContext(DateTime value) {
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

List<String> buildContextInstructionFragments({
  required String currentTime,
  required String location,
  required String personalInstructions,
}) {
  final fragments = <String>[];
  final normalizedCurrentTime = currentTime.trim();
  final normalizedLocation = location.trim();
  final normalizedInstructions = personalInstructions.trim();

  if (normalizedCurrentTime.isNotEmpty) {
    fragments.add('Current local time for the user: $normalizedCurrentTime');
  }
  if (normalizedLocation.isNotEmpty) {
    fragments.add('Current user location: $normalizedLocation');
  }
  if (normalizedInstructions.isNotEmpty) {
    fragments.add(
      'User-specific instructions to follow when relevant:\n'
      '$normalizedInstructions',
    );
  }
  return fragments;
}

int estimateHiddenContextChars({
  required String currentTime,
  required String location,
  required String personalInstructions,
}) {
  return buildContextInstructionFragments(
    currentTime: currentTime,
    location: location,
    personalInstructions: personalInstructions,
  ).fold<int>(0, (sum, fragment) => sum + fragment.length);
}

int estimateToolCallChars(String? name, Map<String, dynamic> args) {
  final normalizedName = (name ?? '').trim();
  if (normalizedName.isEmpty) {
    return 0;
  }
  return normalizedName.length + canonicalJson(args).length;
}

String formatTokenEstimate(int tokens) {
  if (tokens < 1000) {
    return '$tokens tokens';
  }
  if (tokens < 1000000) {
    return '${_trimDecimal(tokens / 1000)}k tokens';
  }
  return '${_trimDecimal(tokens / 1000000)}M tokens';
}

ContextUsageLevel classifyContextUsage(int tokens) {
  if (tokens >= highContextUsageThresholdTokens) {
    return ContextUsageLevel.high;
  }
  if (tokens >= lowContextUsageThresholdTokens) {
    return ContextUsageLevel.medium;
  }
  return ContextUsageLevel.low;
}

double contextUsageProgress(int tokens) {
  return math.min(math.max(tokens, 0), maxContextUsageBarTokens) /
      maxContextUsageBarTokens;
}

String canonicalJson(Object? value) {
  return jsonEncode(_canonicalizeJsonValue(value));
}

Object? _canonicalizeJsonValue(Object? value) {
  if (value is Map) {
    final entries = value.entries.toList()
      ..sort((left, right) => '${left.key}'.compareTo('${right.key}'));
    return {
      for (final entry in entries)
        '${entry.key}': _canonicalizeJsonValue(entry.value),
    };
  }
  if (value is List) {
    return value.map(_canonicalizeJsonValue).toList(growable: false);
  }
  return value;
}

String _trimDecimal(double value) {
  final formatted = value >= 100
      ? value.toStringAsFixed(0)
      : value.toStringAsFixed(1);
  return formatted.endsWith('.0')
      ? formatted.substring(0, formatted.length - 2)
      : formatted;
}

String _twoDigits(int value) => value.toString().padLeft(2, '0');
