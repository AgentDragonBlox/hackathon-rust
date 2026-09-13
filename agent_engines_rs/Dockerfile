FROM rust:1.98-slim AS build
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY agent_engines_rs ./agent_engines_rs
RUN cargo build --release --locked --bin agent_engines

FROM debian:trixie-slim
COPY --from=build /app/target/release/agent_engines /usr/local/bin/agent_engines
ENV PORT=8002
EXPOSE 8002
USER 65532:65532
CMD ["agent_engines"]
