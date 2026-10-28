import 'dart:convert';
import 'package:http/http.dart' as http;

// Set with: --dart-define=API_BASE=http://localhost:8000
// (Android emulator: http://10.0.2.2:8000)
const apiBase = String.fromEnvironment('API_BASE', defaultValue: 'http://localhost:8000');
const userId = 1;

String fmtK(int ngwee) {
  final sign = ngwee < 0 ? '-' : '';
  final n = ngwee.abs();
  final whole = n ~/ 100;
  final frac = n % 100;
  final s = whole.toString().replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (m) => ',');
  return frac == 0 ? '${sign}K$s' : '${sign}K$s.${frac.toString().padLeft(2, '0')}';
}

class ApiError implements Exception {
  final String message;
  ApiError(this.message);
  @override
  String toString() => message;
}

Map<String, dynamic> decodeResponse(http.Response r) {
  // Decode bytes as UTF-8 (the default would garble dashes and accents).
  dynamic j;
  try {
    j = jsonDecode(utf8.decode(r.bodyBytes));
  } catch (_) {
    throw ApiError('Server returned ${r.statusCode}');
  }
  if (r.statusCode != 200) {
    final d = j is Map ? j['detail'] : null;
    throw ApiError(d?.toString() ?? 'Server returned ${r.statusCode}');
  }
  return j as Map<String, dynamic>;
}

Future<Map<String, dynamic>> apiGet(String path) async {
  final r = await http.get(Uri.parse('$apiBase$path')).timeout(const Duration(seconds: 30));
  return decodeResponse(r);
}

Future<Map<String, dynamic>> apiPost(String path, Map<String, dynamic> body) async {
  final r = await http
      .post(Uri.parse('$apiBase$path'),
          headers: {'Content-Type': 'application/json'}, body: jsonEncode(body))
      .timeout(const Duration(seconds: 60));
  return decodeResponse(r);
}

Future<Map<String, dynamic>> apiUpload(
    String path, Map<String, String> fields, List<int> bytes, String filename) async {
  final req = http.MultipartRequest('POST', Uri.parse('$apiBase$path'))
    ..fields.addAll(fields)
    ..files.add(http.MultipartFile.fromBytes('image', bytes, filename: filename));
  final streamed = await req.send().timeout(const Duration(seconds: 90));
  return decodeResponse(await http.Response.fromStream(streamed));
}