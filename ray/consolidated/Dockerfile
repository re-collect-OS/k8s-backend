FROM --platform=linux/amd64 rayproject/ray:2.9.3.cd4d0d-py310-cu121
RUN sudo apt-get update && sudo apt-get install -y ffmpeg tesseract-ocr libtesseract-dev libgl1-mesa-glx
RUN pip install --no-cache-dir -U pip
RUN pip install --no-cache-dir py-spy
RUN pip install --no-cache-dir gliner
RUN pip install --no-cache-dir scikit-learn
RUN pip install --no-cache-dir keybert
RUN pip install --no-cache-dir -U pip opencv-python
RUN pip install --no-cache-dir pytesseract Pillow
RUN pip install torch torchvision torchaudio
RUN pip install --no-cache-dir -U transformers sentence-transformers
RUN pip install --no-cache-dir -U fastapi loguru pydantic
RUN pip install --no-cache-dir -U spacy starlette jiwer invoke vtt_to_srt3 srt
RUN pip install --no-cache-dir -U pandas smart_open boto3 awscli loguru pyOpenSSL cryptography
RUN python -m spacy download en_core_web_sm
RUN python -m spacy download en_core_web_lg
RUN pip install --no-cache-dir openai-whisper==20230124
RUN git clone https://github.com/ytdl-org/youtube-dl.git && cd youtube-dl && pip install .
RUN export PATH=$PATH:/usr/local/cuda/bin
RUN export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/cuda/lib64:/usr/local/cuda/lib
COPY models /home/ray
COPY summarize.py /home/ray
RUN sudo chown root:root `which py-spy`
RUN sudo chmod u+s `which py-spy`
