# recorder.py
import wave
import os
import logging
from PySide6.QtCore import QObject, Signal, QMutex, QMutexLocker
from PySide6.QtMultimedia import (
    QAudioDevice,
    QAudioSource,
    QMediaDevices,
    QAudioFormat,
    QAudio
)
import numpy as np
from config import RECORDER_PREFERRED_RATES

logger = logging.getLogger(__name__)

class AudioRecorder(QObject):
    started = Signal()
    stopped = Signal()
    errorOccurred = Signal(str)
    levelChanged = Signal(float)

    def __init__(self, audio_device: QAudioDevice = None, parent=None):
        super().__init__(parent)
        self._is_recording = False
        self._audio_source = None
        self._io_device = None
        self._output_file = None
        self._raw_path = ""
        self._final_path = ""
        
        self._buffer = b''
        self._buffer_mutex = QMutex()

        self._is_valid = False
        self._init_error_message = ""

        self._source_format = QAudioFormat()
        self._target_format = QAudioFormat()
        self._target_format.setSampleRate(24000)
        self._target_format.setChannelCount(1)
        self._target_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        
        available_devices = QMediaDevices.audioInputs()
        if not available_devices:
            self._init_error_message = "利用可能なマイクが見つかりません。マイクが接続され、OSに認識されているか確認してください。"
            return

        target_device = audio_device if audio_device else QMediaDevices.defaultAudioInput()
        
        preferred_sample_rates = RECORDER_PREFERRED_RATES
        preferred_formats = [
            QAudioFormat.SampleFormat.Float, 
            QAudioFormat.SampleFormat.Int32, 
            QAudioFormat.SampleFormat.Int16
        ]
        
        supported_format_found = False
        for rate in preferred_sample_rates:
            for sample_format in preferred_formats:
                test_format = QAudioFormat()
                test_format.setSampleRate(rate)
                test_format.setChannelCount(1)
                test_format.setSampleFormat(sample_format)
                
                if target_device.isFormatSupported(test_format):
                    self._source_format = test_format
                    self._target_format.setSampleRate(rate)
                    supported_format_found = True
                    
                    if sample_format == QAudioFormat.SampleFormat.Float:
                        bit_depth_desc = "Float32"
                    elif sample_format == QAudioFormat.SampleFormat.Int32:
                        bit_depth_desc = "Int32"
                    else:
                        bit_depth_desc = "Int16"
                    
                    logger.info(f"Audio format selected: {rate} Hz, {bit_depth_desc} Mono (will be saved as 16-bit PCM)")
                    break
            if supported_format_found:
                break

        if not supported_format_found:
            self._init_error_message = f"選択されたマイクは、モノラルFloat32, Int32, Int16のいずれの音声形式もサポートしていないようです。"
            return
            
        self._audio_source = QAudioSource(target_device, self._source_format, self)
        self._audio_source.stateChanged.connect(self._handle_source_state_change)

        self._is_valid = True

    def is_valid(self) -> bool:
        return self._is_valid

    def get_init_error(self) -> str:
        return self._init_error_message

    def _handle_source_state_change(self, state: QAudio.State):
        if self._is_recording and state in (QAudio.State.IdleState, QAudio.State.StoppedState):
            msg = "録音ソースが予期せず停止しました。マイクが切断されたか、無効になった可能性があります。"
            self.errorOccurred.emit(msg)
            self.stop()

    def _handle_ready_read(self):
        if not self._io_device or not self._output_file:
            return

        locker = QMutexLocker(self._buffer_mutex)
        
        data = self._buffer + self._io_device.readAll()
        if not data:
            return

        sample_size = self._source_format.bytesPerSample()
        if sample_size <= 0:
            self._buffer = b''
            return

        remainder_len = len(data) % sample_size
        processable_len = len(data) - remainder_len
        
        data_to_process = data[:processable_len]
        self._buffer = data[processable_len:]

        locker.unlock()

        if not data_to_process:
            return

        source_sample_format = self._source_format.sampleFormat()
        normalized_level = 0.0
        samples_16bit = None

        try:
            if source_sample_format == QAudioFormat.SampleFormat.Float:
                samples_f = np.frombuffer(data_to_process, dtype=np.float32)
                if samples_f.size > 0:
                    normalized_level = np.max(np.abs(samples_f))
                    samples_f_clipped = np.clip(samples_f, -0.999969, 0.999969)
                    samples_16bit = np.round(samples_f_clipped * 32767.0).astype(np.int16)

            elif source_sample_format == QAudioFormat.SampleFormat.Int32:
                samples_i32 = np.frombuffer(data_to_process, dtype=np.int32)
                if samples_i32.size > 0:
                    samples_f = samples_i32.astype(np.float32) / 2147483647.0
                    normalized_level = np.max(np.abs(samples_f))
                    samples_f_clipped = np.clip(samples_f, -0.999969, 0.999969)
                    samples_16bit = np.round(samples_f_clipped * 32767.0).astype(np.int16)

            elif source_sample_format == QAudioFormat.SampleFormat.Int16:
                samples_16bit = np.frombuffer(data_to_process, dtype=np.int16)
                if samples_16bit.size > 0:
                    samples_i32 = samples_16bit.astype(np.int32)
                    normalized_level = np.max(np.abs(samples_i32)) / 32767.0
            
            else:
                raise RuntimeError(f"Unsupported audio sample format received: {source_sample_format}")

            if samples_16bit is not None and samples_16bit.size > 0:
                normalized_level = np.clip(normalized_level, 0.0, 1.0)
                
                self.levelChanged.emit(normalized_level)
                self._output_file.write(samples_16bit.tobytes())
                
        except Exception as e:
            error_msg = f"音声バッファの処理中にエラーが発生しました: {e}"
            self.errorOccurred.emit(error_msg)

    def start(self, out_path: str):
        if self._is_recording or not self._is_valid:
            return
            
        self._final_path = out_path
        self._raw_path = out_path + ".raw"

        locker = QMutexLocker(self._buffer_mutex)
        self._buffer = b''
        locker.unlock()

        try:
            self._output_file = open(self._raw_path, 'wb')
            self._io_device = self._audio_source.start()
            if not self._io_device:
                raise IOError("Audio source did not return a valid IO device.")
        except Exception as e:
            if self._output_file:
                try:
                    self._output_file.close()
                except IOError:
                    pass
                self._output_file = None
            self.errorOccurred.emit(f"録音の開始に失敗しました: {e}")
            return

        self._io_device.readyRead.connect(self._handle_ready_read)
        self._is_recording = True
        self.started.emit()

    def stop(self):
        if not self._is_recording or not self._audio_source:
            return

        try:
            self._is_recording = False
            self.stopped.emit()
            
            self._audio_source.stop()
            
            if self._io_device:
                try:
                    self._io_device.readyRead.disconnect(self._handle_ready_read)
                except RuntimeError:
                    pass
                
                self._handle_ready_read()
            
            locker = QMutexLocker(self._buffer_mutex)
            if self._buffer and self._output_file:
                sample_size = self._source_format.bytesPerSample()
                if sample_size > 0:
                    padding_needed = (sample_size - len(self._buffer) % sample_size) % sample_size
                    final_padded_data = self._buffer + (b'\x00' * padding_needed)
                    
                    from PySide6.QtCore import QBuffer, QIODevice
                    temp_buffer_device = QBuffer()
                    temp_buffer_device.setData(final_padded_data)
                    temp_buffer_device.open(QIODevice.OpenModeFlag.ReadOnly)
                    
                    original_io_device = self._io_device
                    self._io_device = temp_buffer_device
                    
                    locker.unlock()
                    
                    try:
                        self._handle_ready_read()
                    finally:
                        self._io_device = original_io_device

        except Exception as e:
            logger.error(f"Error during final buffer drain: {e}")
        finally:
            self._io_device = None
            if self._output_file:
                try:
                    self._output_file.close()
                except IOError as e:
                     logger.error(f"Error closing raw audio file: {e}")
                self._output_file = None
            
        self._write_wav_header()

    def _write_wav_header(self):
        if not os.path.exists(self._raw_path):
            if self._final_path:
                logger.warning(f"Raw file missing, cannot write WAV header: {self._raw_path}")
            return

        if os.path.getsize(self._raw_path) == 0:
            logger.warning("No raw data captured, skipping WAV header write.")
            try:
                os.remove(self._raw_path)
            except OSError:
                pass
            return

        try:
            with open(self._raw_path, 'rb') as raw_file, wave.open(self._final_path, 'wb') as wav_file:
                wav_file.setnchannels(self._target_format.channelCount())
                sample_width_bytes = self._target_format.bytesPerFrame() // self._target_format.channelCount()
                wav_file.setsampwidth(sample_width_bytes)
                wav_file.setframerate(self._target_format.sampleRate())
                
                chunk_size = 8192 # Process in 8KB chunks
                while True:
                    chunk = raw_file.read(chunk_size)
                    if not chunk:
                        break
                    wav_file.writeframes(chunk)
            
            if os.path.exists(self._raw_path):
                try:
                    os.remove(self._raw_path)
                    logger.info(f"Successfully converted and removed temporary raw file: {self._raw_path}")
                except OSError as e:
                    logger.warning(f"Failed to remove temporary raw file: {e}")

        except Exception as e:
            self.errorOccurred.emit(f"WAVファイルの保存に失敗しました: {e}")
            logger.error(f"Failed to write WAV file. Raw data left at {self._raw_path} for potential recovery. Reason: {e}", exc_info=True)

            if os.path.exists(self._raw_path):
                try:
                    os.remove(self._raw_path)
                    logger.info(f"Cleaned up temporary raw file after error: {self._raw_path}")
                except OSError as cleanup_e:
                    logger.warning(f"Failed to clean up temporary raw file after error: {cleanup_e}")

    def is_recording(self) -> bool:
        return self._is_recording