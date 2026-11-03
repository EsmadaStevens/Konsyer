import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'package:image_picker/image_picker.dart';

// Set with: --dart-define=API_BASE=http://localhost:8000
// (Android emulator: http://10.0.2.2:8000)
const apiBase = String.fromEnvironment('API_BASE', defaultValue: 'http://localhost:8000');
const userId = 1;

/// True when the backend answers from recordings. Replay finds a photo by its file name, and on the web the picker
/// loses the name when it shrinks a photo, so in replay we pick the original file untouched.
bool replayMode = false;

Future<XFile?> pickPhoto(ImageSource source, {int maxWidth = 1600, int quality = 85}) {
  return replayMode
      ? ImagePicker().pickImage(source: source)
      : ImagePicker().pickImage(source: source, maxWidth: maxWidth.toDouble(), imageQuality: quality);
}

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

Future<Map<String, dynamic>> apiGet(String path, {Map<String, String>? headers}) async {
  final r = await http
      .get(Uri.parse('$apiBase$path'), headers: headers)
      .timeout(const Duration(seconds: 30));
  return decodeResponse(r);
}

Future<Map<String, dynamic>> apiPost(String path, Map<String, dynamic> body,
    {Map<String, String>? headers}) async {
  final r = await http
      .post(Uri.parse('$apiBase$path'),
          headers: {'Content-Type': 'application/json', ...?headers}, body: jsonEncode(body))
      .timeout(const Duration(seconds: 60));
  return decodeResponse(r);
}

Future<Map<String, dynamic>> apiUpload(
    String path, Map<String, String> fields, List<int> bytes, String filename,
    {String field = 'image'}) async {
  final req = http.MultipartRequest('POST', Uri.parse('$apiBase$path'))
    ..fields.addAll(fields)
    ..files.add(http.MultipartFile.fromBytes(field, bytes, filename: filename));
  final streamed = await req.send().timeout(const Duration(seconds: 90));
  return decodeResponse(await http.Response.fromStream(streamed));
}

/// Raw bytes, for a photo a person must look at before deciding. Never cached by the server.
Future<Uint8List> apiGetBytes(String path, {Map<String, String>? headers}) async {
  final r = await http
      .get(Uri.parse('$apiBase$path'), headers: headers)
      .timeout(const Duration(seconds: 30));
  if (r.statusCode != 200) throw ApiError('The photo is no longer available.');
  return r.bodyBytes;
}
