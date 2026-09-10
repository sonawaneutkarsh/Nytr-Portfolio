import Foundation

/// Reads the body of a URLRequest captured by a stubbed URLProtocol.
///
/// M11B toolchain compatibility: newer URLSession implementations deliver the
/// request body via `httpBodyStream` (leaving `httpBody` nil), so stub
/// handlers must support both paths. Test-only helper; no production use.
enum StubRequestBody {
    static func data(from request: URLRequest) -> Data {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let bufferSize = 4096
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let read = stream.read(buffer, maxLength: bufferSize)
            if read <= 0 { break }
            data.append(buffer, count: read)
        }
        return data
    }
}