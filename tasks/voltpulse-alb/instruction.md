# VoltPulse: the demo URL is dead

We just moved our backend and database into private subnets, and now nobody can reach the app anymore. "Tom, the demo URL is dead." We need a proper front door instead of the public IP we used to abuse.

Put an internet-facing Application Load Balancer in front of the backend. It should live in the two public subnets, accept plain HTTP on port 80 from anywhere, and forward to the backend instance on port 8080, which is where the app listens — health-check it on `/`.

Lock it down the right way while you're at it: the backend's security group should allow 8080 only from the load balancer's security group, never from an IP range. The backend and the database stay private exactly as they are.
