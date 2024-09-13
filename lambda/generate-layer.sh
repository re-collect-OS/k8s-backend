# https://dev.to/mmascioni/using-external-python-packages-with-aws-lambda-layers-526o

mkdir python

docker run --rm \
--volume=$(pwd):/lambda-build \
-w=/lambda-build \
lambci/lambda:build-python3.8 \
pip install -r requirements.txt --target python

zip -r requests-layer python/

# Upload to s3://recollect-lambda-layers/requests-layer.zip
