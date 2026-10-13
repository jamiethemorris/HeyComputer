import AVFoundation
import SwiftUI

struct QRScanner: UIViewControllerRepresentable {
    let completion: (Result<String, Error>) -> Void
    func makeUIViewController(context: Context) -> ScannerController {
        let controller = ScannerController()
        controller.completion = completion
        return controller
    }
    func updateUIViewController(_ controller: ScannerController, context: Context) {}
    static func dismantleUIViewController(_ controller: ScannerController, coordinator: ()) { controller.stop() }
}

final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var completion: ((Result<String, Error>) -> Void)?
    private let capture = AVCaptureSession()
    private let queue = DispatchQueue(label: "HeyComputer.QRScanner")
    private var preview: AVCaptureVideoPreviewLayer?
    private var finished = false
    private var closed = false // accessed only on the capture queue

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        let preview = AVCaptureVideoPreviewLayer(session: capture)
        preview.videoGravity = .resizeAspectFill
        view.layer.addSublayer(preview)
        self.preview = preview
        let close = UIButton(type: .system)
        close.setTitle("Cancel", for: .normal)
        close.tintColor = .white
        close.backgroundColor = UIColor.black.withAlphaComponent(0.6)
        close.addTarget(self, action: #selector(cancel), for: .touchUpInside)
        close.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(close)
        NSLayoutConstraint.activate([
            close.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 16),
            close.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20),
            close.widthAnchor.constraint(equalToConstant: 90), close.heightAnchor.constraint(equalToConstant: 44)
        ])
        AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
            guard let self else { return }
            if granted { self.configure() }
            else { DispatchQueue.main.async { self.fail("Allow camera access in Settings to scan, or enter the pairing details manually.") } }
        }
    }
    override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews(); preview?.frame = view.bounds }
    @objc private func cancel() { stop(); dismiss(animated: true) }
    func stop() { queue.async { self.closed = true; self.capture.stopRunning() } }
    private func fail(_ message: String) {
        guard !finished else { return }
        finished = true
        stop()
        completion?(.failure(RemoteFailure.message(message)))
    }
    private func configure() {
        queue.async {
            guard !self.closed else { return }
            do {
                guard let camera = AVCaptureDevice.default(for: .video) else {
                    throw RemoteFailure.message("No camera available. Enter the pairing details manually.")
                }
                let input = try AVCaptureDeviceInput(device: camera)
                let output = AVCaptureMetadataOutput()
                guard self.capture.canAddInput(input), self.capture.canAddOutput(output) else {
                    throw RemoteFailure.message("Could not start the camera.")
                }
                self.capture.addInput(input)
                self.capture.addOutput(output)
                output.setMetadataObjectsDelegate(self, queue: .main)
                output.metadataObjectTypes = [.qr]
                self.capture.startRunning()
            } catch { DispatchQueue.main.async { self.fail(error.localizedDescription) } }
        }
    }
    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject],
                        from connection: AVCaptureConnection) {
        guard !finished, let value = metadataObjects.compactMap({ ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }).first else { return }
        finished = true
        stop()
        completion?(.success(value))
    }
}
