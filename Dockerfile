# --- FASE 1: BUILDER ---
# Usiamo l'immagine base per scaricare le wheels
FROM kathara/frr:latest AS builder

# 1. Installa pip
RUN apt-get update && \
    apt-get install -y python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 2. Scarica le wheels
WORKDIR /wheels
RUN pip download \
    grpcio \
    grpcio-tools \
    protobuf \
    typing_extensions


# --- FASE 2: IMMAGINE FINALE (offline) ---
FROM kathara/frr:latest

# 3. Installa Python e Venv
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# 4. Crea la VENV
RUN python3 -m venv venv

# 5. Copia le wheels scaricate dalla FASE 1
COPY --from=builder /wheels /tmp/wheels

# 6. Installa le wheels nella VENV
RUN venv/bin/pip install --no-index --find-links=/tmp/wheels \
    grpcio grpcio-tools protobuf typing_extensions

# 9. Imposta il PATH (come prima)
ENV PATH="/venv/bin:$PATH"

# 10. Pulisci i file temporanei
RUN rm -rf /tmp/wheels
