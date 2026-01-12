#!/usr/bin/env node
/**
 * MeshCore packet decoder wrapper.
 *
 * Usage: node meshcore_decode.mjs <hex_string>
 *
 * Decodes a MeshCore packet from hex and extracts location, role, and routing info.
 * Outputs JSON to stdout.
 */

import { MeshCoreDecoder, getDeviceRoleName } from '@michaelhart/meshcore-decoder';

const hex = (process.argv[2] || '').trim();

function pickLocation(decodedPacket) {
  const payloadDecoded = decodedPacket?.payload?.decoded ?? null;
  const payloadRoot = decodedPacket?.payload ?? null;
  const appData = payloadDecoded?.appData ?? payloadDecoded?.appdata ?? payloadRoot?.appData ?? payloadRoot?.appdata ?? null;
  const loc = appData?.location ?? payloadDecoded?.location ?? payloadRoot?.location ?? null;
  const lat = loc?.latitude ?? loc?.lat ?? null;
  const lon = loc?.longitude ?? loc?.lon ?? null;
  const name = appData?.name ?? payloadDecoded?.name ?? payloadRoot?.name ?? null;
  const pubkey =
    payloadDecoded?.publicKey ??
    payloadDecoded?.publickey ??
    payloadRoot?.publicKey ??
    payloadRoot?.publickey ??
    decodedPacket?.publicKey ??
    decodedPacket?.publickey ??
    null;
  return { lat, lon, name, pubkey };
}

function pickRole(decodedPacket) {
  const payloadDecoded = decodedPacket?.payload?.decoded ?? null;
  const payloadRoot = decodedPacket?.payload ?? null;
  const appData = payloadDecoded?.appData ?? payloadDecoded?.appdata ?? payloadRoot?.appData ?? payloadRoot?.appdata ?? null;
  const candidates = [
    appData?.role,
    appData?.deviceRole,
    appData?.nodeRole,
    appData?.deviceType,
    appData?.nodeType,
    appData?.class,
    appData?.profile,
    payloadDecoded?.role,
    payloadDecoded?.deviceRole,
    payloadDecoded?.nodeRole,
    payloadDecoded?.deviceType,
    payloadDecoded?.nodeType,
    payloadDecoded?.class,
    payloadDecoded?.profile,
    payloadRoot?.role,
    payloadRoot?.deviceRole,
    payloadRoot?.nodeRole,
    payloadRoot?.deviceType,
    payloadRoot?.nodeType,
    payloadRoot?.class,
    payloadRoot?.profile,
  ];
  for (const value of candidates) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
}

try {
  const decoded = MeshCoreDecoder.decode(hex);
  const loc = pickLocation(decoded);
  const payloadDecoded = decoded?.payload?.decoded ?? decoded?.payload ?? null;
  const payloadRoot = decoded?.payload ?? null;
  const appData = payloadDecoded?.appData ?? payloadDecoded?.appdata ?? payloadRoot?.appData ?? payloadRoot?.appdata ?? null;
  const deviceRole = appData?.deviceRole ?? payloadDecoded?.deviceRole ?? payloadRoot?.deviceRole ?? null;
  const deviceRoleName = typeof deviceRole === 'number' ? getDeviceRoleName(deviceRole) : null;
  const role = pickRole(decoded) || deviceRoleName;
  const payloadKeys = payloadDecoded && typeof payloadDecoded === 'object' ? Object.keys(payloadDecoded) : null;
  const appDataKeys = appData && typeof appData === 'object' ? Object.keys(appData) : null;
  const pathHashes = payloadDecoded?.pathHashes ?? null;
  const snrValues = payloadDecoded?.snrValues ?? null;
  const path = decoded?.path ?? null;
  const pathLength = decoded?.pathLength ?? null;
  const out = {
    ok: true,
    payloadType: decoded?.payloadType ?? null,
    routeType: decoded?.routeType ?? null,
    messageHash: decoded?.messageHash ?? null,
    location: loc,
    role,
    deviceRole,
    deviceRoleName,
    payloadKeys,
    appDataKeys,
    pathHashes,
    snrValues,
    path,
    pathLength,
  };
  console.log(JSON.stringify(out));
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: String(e) }));
}
