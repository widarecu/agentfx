import { defineRailway, github, project, service } from "railway/iac";

// Staging backend service settings (AFX-139), the only source of them. The
// Deploy workflow applies this file before each backend deploy, so change
// these settings here, never in the Railway dashboard.
//
// This repository manages only its own resources in the environment.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
export const partial = "agentfx";

export default defineRailway(() => {
  const agentfx = service("agentfx", {
    source: github("widarecu/agentfx"),
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/health",
    replicas: { "asia-southeast1-eqsg3a": 1 },
    deploy: {
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      sleepApplication: true,
    },
  });

  return project("lively-fascination", {
    variables: { managed: false },
    resources: [agentfx],
  });
});
