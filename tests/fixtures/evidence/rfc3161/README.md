RFC 3161 sample: data.txt is time-stamped by response.tsr from a synthetic TSA (tsa.pem). Check: openssl ts -verify -data data.txt -in response.tsr -CAfile tsa.pem
